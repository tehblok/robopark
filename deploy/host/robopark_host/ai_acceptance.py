"""Short, read-only inference load probe for the actual managed AGX runtime."""

from __future__ import annotations

import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import ai_runtime

MAX_RESPONSE = 65536


def percentile(values, fraction):
    return (
        sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]
        if values
        else None
    )


def sample(request, *, clock=time.monotonic):
    started = clock()
    try:
        value = request()
        choices = value.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("response")
        choice = choices[0]
        message = choice.get("message", {})
        valid = (
            choice.get("finish_reason") == "stop"
            and message.get("role") == "assistant"
            and isinstance(message.get("content"), str)
            and bool(message["content"].strip())
            and not message.get("tool_calls")
        )
        usage = value.get("usage", {})
        tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        if type(tokens) is not int or tokens < 0:
            tokens = None
        return {"ok": valid, "seconds": clock() - started, "completion_tokens": tokens}
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        # Neither private keys nor server-provided error bodies belong in reports.
        return {"ok": False, "seconds": clock() - started, "completion_tokens": None}


def run_stage(request, concurrency, *, clock=time.monotonic):
    if type(concurrency) is not int or not 1 <= concurrency <= 15:
        raise ValueError("ai_probe_concurrency_invalid")
    started = clock()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(sample, request, clock=clock) for _ in range(concurrency)
        ]
        samples = [future.result() for future in futures]
    elapsed = clock() - started
    latencies = [item["seconds"] for item in samples]
    known_tokens = [item["completion_tokens"] for item in samples]
    return {
        "concurrent_requests": concurrency,
        "completed": sum(item["ok"] for item in samples),
        "failed_or_truncated": sum(not item["ok"] for item in samples),
        "wall_seconds": round(elapsed, 3),
        "latency_p50_seconds": round(percentile(latencies, 0.5), 3),
        "latency_p95_seconds": round(percentile(latencies, 0.95), 3),
        "aggregate_output_tokens_per_second": (
            round(sum(known_tokens) / elapsed, 3)
            if elapsed > 0 and all(token is not None for token in known_tokens)
            else None
        ),
        "samples": samples,
    }


def _memory(paths):
    try:
        lines = (paths.root / "proc/meminfo").read_text().splitlines()
        return {
            line.split(":", 1)[0]: int(line.split()[1])
            for line in lines
            if line.startswith(("MemTotal:", "MemAvailable:", "SwapFree:"))
        }
    except (OSError, ValueError, IndexError):
        return None


def run(paths, *, concurrency=(1, 5, 15)):
    """Use only synthetic text; no task, connector, script or robot is modified."""
    stages = tuple(concurrency)
    if (
        not stages
        or len(stages) > 3
        or any(type(n) is not int or not 1 <= n <= 15 for n in stages)
    ):
        raise ValueError("ai_probe_concurrency_invalid")
    supported, reason = ai_runtime.probe_support(paths)
    if not supported:
        return {"schema": 1, "state": "unsupported", "reason": reason}
    if not ai_runtime.installed(paths, verify=True):
        return {"schema": 1, "state": "unavailable", "reason": "not_installed"}
    ready, reason = ai_runtime.runtime_ready(paths)
    if not ready:
        return {"schema": 1, "state": "unavailable", "reason": reason}
    key = ai_runtime.read_api_key(paths)
    payload = json.dumps(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "Ответь по-русски двумя короткими предложениями. Используй только факты из сообщения; не придумывай результат проверки.",
                },
                {
                    "role": "user",
                    "content": "Учебный пример: модуль заменили. После замены ошибка осталась. Позже решили проверить кабель, но результата проверки нет. Что выполнено и что неизвестно?",
                },
            ],
            "max_tokens": 128,
            "temperature": 0,
            "stream": False,
            "reasoning_effort": "none",
        }
    ).encode()

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request():
        request = urllib.request.Request(
            "http://127.0.0.1:18081/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + key,
            },
            method="POST",
        )
        with opener.open(request, timeout=120) as response:
            raw = response.read(MAX_RESPONSE + 1)
            if response.status != 200 or len(raw) > MAX_RESPONSE:
                raise ValueError("ai_probe_response_invalid")
            return json.loads(raw)

    before = _memory(paths)
    results = [run_stage(request, level) for level in stages]
    selection = ai_runtime.selected_model(paths)
    return {
        "schema": 1,
        "state": "measured"
        if all(not r["failed_or_truncated"] for r in results)
        else "failed",
        "model": selection["model"],
        "model_family": selection["model_family"],
        "model_source": selection["model_source"],
        "model_sha256": selection["model_sha256"],
        "llama_commit": ai_runtime.LLAMA_COMMIT,
        "parallel_slots": ai_runtime.PARALLEL_SLOTS,
        "context_tokens_per_slot": ai_runtime.CONTEXT_TOKENS_PER_SLOT,
        "total_context_tokens": ai_runtime.TOTAL_CONTEXT_TOKENS,
        "layer": "native_inference_only",
        "semantic_quality_evaluated": False,
        "application_queue_evaluated": False,
        "ttft_measured": False,
        "memory_before_kib": before,
        "memory_after_kib": _memory(paths),
        "stages": results,
    }
