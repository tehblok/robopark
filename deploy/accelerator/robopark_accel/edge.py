"""Pinned, optional model-neutral Edge-LLM experiment server on loopback."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import stat
import urllib.request
from dataclasses import replace
from pathlib import Path

from . import registry

VERSION = "0.11.0"
COMMIT = "95515c2f87fba8982db5a519f9022277667b3cc9"


def api_key(state: Path) -> str:
    path = state / "edge-api-key"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise ValueError("edge_api_key_permissions")
    value = path.read_text().strip()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("edge_api_key_invalid")
    return value


def launch_arguments(state: Path) -> list[str]:
    profile = registry.read_json(state / "edge-profile.json")
    if profile.get("schema") != 1 or profile.get("runtime_version") != VERSION:
        raise ValueError("edge_profile_invalid")
    model = registry.verify(state, profile["model_id"])
    if model["kind"] != "checkpoint" or model["sha256"] != profile["model_sha256"]:
        raise ValueError("edge_model_identity_mismatch")
    if not Path(model["path"]).is_relative_to((state / "models").resolve()):
        raise ValueError("edge_checkpoint_requires_managed_models_directory")
    if profile.get("adapter") is not None or profile.get("rag", {}).get("enabled") is not False:
        raise ValueError("edge_adapter_or_rag_requires_application_integration")
    parser = profile.get("tool_parser")
    if parser not in {"auto", "generic", "hermes", "qwen3_xml", "nemotron", "openai"}:
        raise ValueError("tool_parser_invalid")
    # Bound every deployment setting; an edited profile cannot expose a listener
    # or expand the shared-memory budget through arbitrary CLI arguments.
    fixed = {
        "host": "127.0.0.1",
        "port": 18082,
        "max_input_len": 4096,
        "max_kv_cache_capacity": 8192,
        "max_batch_size": 1,
    }
    if any(profile.get(key) != value for key, value in fixed.items()):
        raise ValueError("edge_profile_limits_invalid")
    return [
        model["path"],
        "--host",
        "127.0.0.1",
        "--port",
        "18082",
        "--served-model-name",
        model["id"],
        "--cache-dir",
        str(state / "edge-cache"),
        "--engine-cache-max-size-gb",
        "20",
        "--max-input-len",
        "4096",
        "--max-kv-cache-capacity",
        "8192",
        "--max-batch-size",
        "1",
        "--max-queued-requests",
        "4",
        "--queue-timeout",
        "120",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        parser,
    ]


def serve(state: Path) -> None:
    if os.geteuid() == 0:
        raise ValueError("run_as_robopark_accel_not_root")
    arguments = launch_arguments(state)
    if importlib.metadata.version("tensorrt-edgellm") != VERSION:
        raise ValueError("edge_runtime_version_mismatch")
    # Use JetPack's TensorRT bindings. Standalone DLA experiments belong to a
    # separate environment; upgrading its TRT must not silently alter this LLM.
    import tensorrt

    if not re.match(r"^10\.16\.2(?:\.|$)", tensorrt.__version__):
        raise ValueError("edge_requires_validated_tensorrt_10_16_2")
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HOME=str(state / "edge-cache/huggingface"),
        CUDA_CACHE_PATH=str(state / "edge-cache/cuda"),
    )
    from experimental.server.api.app import run_http_server
    from experimental.server.config import parse_server_config
    from experimental.server.runtime.engine import load_model
    from experimental.server.runtime.engine_client import EngineClient

    config = parse_server_config(arguments)
    # Never pass the secret through process argv, shell or a readable unit file.
    api = replace(config.api, api_key=api_key(state))
    llm = load_model(**config.model.llm_kwargs())
    run_http_server(EngineClient(llm, api), api)


def _request(state: Path, route: str, payload: dict | None = None) -> dict:
    request = urllib.request.Request(
        "http://127.0.0.1:18082" + route,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + api_key(state), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError("edge_response_too_large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("edge_response_invalid")
    return result


def check(state: Path) -> dict:
    launch_arguments(state)
    profile = registry.read_json(state / "edge-profile.json")
    _request(state, "/health/ready")
    models = _request(state, "/v1/models")
    if profile["model_id"] not in [item.get("id") for item in models.get("data", [])]:
        raise ValueError("edge_served_model_mismatch")
    tool = {
        "type": "function",
        "function": {
            "name": "check_contract",
            "description": "Synthetic test",
            "parameters": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
        },
    }
    result = _request(
        state,
        "/v1/chat/completions",
        {
            "model": profile["model_id"],
            "messages": [{"role": "user", "content": "Call check_contract with ok=true."}],
            "tools": [tool],
            "tool_choice": {"type": "function", "function": {"name": "check_contract"}},
            "max_tokens": 128,
            "stream": False,
            "temperature": 0,
            "parallel_tool_calls": False,
        },
    )
    try:
        calls = result["choices"][0]["message"]["tool_calls"]
        valid = (
            len(calls) == 1
            and calls[0]["type"] == "function"
            and calls[0]["function"]["name"] == "check_contract"
            and json.loads(calls[0]["function"]["arguments"]) == {"ok": True}
        )
    except (KeyError, IndexError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("edge_tool_contract_failed")
    return {
        "state": "protocol_smoke_passed",
        "model_id": profile["model_id"],
        "expected_model_sha256": profile["model_sha256"],
        "expected_runtime_version": VERSION,
        "process_identity_verified": False,
        "production_selected": False,
        "checks": ["health", "model_identity", "forced_tool_call"],
    }
