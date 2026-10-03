#!/usr/bin/env python3
"""Resumable real-time soak orchestrator with atomic machine-readable checkpoints."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

COUNT_FIELDS = (
    "fd_count",
    "timer_count",
    "subscription_count",
    "object_url_count",
    "media_track_count",
    "db_pool_checked_out",
)
BYTE_FIELDS = ("browser_rss_bytes", "cache_bytes", "storage_bytes")
SAMPLE_FIELDS = {
    "browser_rss_bytes",
    "fd_count",
    "timer_count",
    "subscription_count",
    "object_url_count",
    "media_track_count",
    "cache_bytes",
    "storage_bytes",
    "db_pool_checked_out",
    "errors",
    "operations",
}
REQUIRED_OPERATIONS = {
    "navigation",
    "mode_switch",
    "task",
    "robot",
    "cache",
    "background",
    "camera",
    "photo",
}
UTC = timezone.utc


def canonical_json(value: dict) -> bytes:
    clean = {key: item for key, item in value.items() if key != "checksum"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()


def checkpoint_digest(value: dict) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def new_checkpoint(
    *, source_tree_sha256: str, duration_seconds: int, chunk_seconds: int
) -> dict:
    if (
        len(source_tree_sha256) != 64
        or any(char not in "0123456789abcdef" for char in source_tree_sha256)
        or duration_seconds <= 0
        or chunk_seconds <= 0
        or chunk_seconds > duration_seconds
    ):
        raise ValueError("checkpoint_config")
    return {
        "format": 1,
        "source_tree_sha256": source_tree_sha256,
        "target_seconds": duration_seconds,
        "chunk_seconds": chunk_seconds,
        "elapsed_seconds": 0,
        "started_at": datetime.now(UTC).isoformat(),
        "samples": [],
    }


def write_checkpoint(path: Path, value: dict) -> None:
    payload = {**value, "checksum": checkpoint_digest(value)}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".soak-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_checkpoint(path: Path) -> dict:
    value = json.loads(path.read_text())
    checksum = value.pop("checksum", None)
    if checksum != checkpoint_digest(value):
        raise ValueError("checkpoint_checksum")
    return value


def _monotonic_growth(values: list[int | float]) -> bool:
    return (
        len(values) >= 3
        and values[-1] > values[0]
        and all(
            after >= before for before, after in zip(values, values[1:])
        )
    )


def evaluate_samples(samples: list[dict], *, warmup_samples: int) -> dict:
    if len(samples) <= warmup_samples:
        return {
            "passed": False,
            "reason": "insufficient_samples",
            "growth": {},
            "monotonic_growth": [],
        }
    settled = samples[warmup_samples:]
    growth = {
        field: settled[-1][field] - settled[0][field]
        for field in (*BYTE_FIELDS, *COUNT_FIELDS)
    }
    monotonic = []
    excessive = []
    for field in (*BYTE_FIELDS, *COUNT_FIELDS):
        values = [sample[field] for sample in settled]
        allowance = (
            max(16 * 1024 * 1024, int(values[0] * 0.10))
            if field == "browser_rss_bytes"
            else max(1024 * 1024, int(values[0] * 0.05))
            if field in {"cache_bytes", "storage_bytes"}
            else 0
        )
        if growth[field] > allowance:
            excessive.append(field)
            if _monotonic_growth(values):
                monotonic.append(field)
    errors = sum(sample.get("errors", 0) for sample in samples)
    return {
        "passed": not excessive and errors == 0,
        "growth": growth,
        "monotonic_growth": monotonic,
        "excessive_growth": excessive,
        "errors": errors,
        "warmup_samples": warmup_samples,
    }


def _validate_sample(value: dict) -> dict:
    if not isinstance(value, dict) or not SAMPLE_FIELDS <= set(value):
        raise ValueError("soak_sample")
    for field in SAMPLE_FIELDS - {"operations"}:
        if (
            type(value[field]) not in (int, float)
            or not math.isfinite(value[field])
            or value[field] < 0
        ):
            raise ValueError("soak_sample")
    operations = value["operations"]
    if not isinstance(operations, list) or not REQUIRED_OPERATIONS <= set(operations):
        raise ValueError("soak_sample")
    return value


def validate_continuous_report(
    value: dict, *, duration_seconds: int, run_token: str
) -> dict:
    """Validate evidence produced by one uninterrupted browser/context run."""
    if (
        not isinstance(value, dict)
        or set(value) != {
            "format",
            "run_token",
            "target_seconds",
            "elapsed_seconds",
            "continuous_contexts",
            "samples",
        }
        or value["format"] != 2
        or value["run_token"] != run_token
        or value["target_seconds"] != duration_seconds
        or value["elapsed_seconds"] != duration_seconds
        or value["continuous_contexts"] != 1
        or not isinstance(value["samples"], list)
        or not value["samples"]
    ):
        raise ValueError("soak_continuous")
    elapsed = 0
    for sample in value["samples"]:
        _validate_sample(sample)
        sample_elapsed = sample.get("elapsed_seconds")
        if type(sample_elapsed) is not int or not elapsed < sample_elapsed <= duration_seconds:
            raise ValueError("soak_continuous")
        elapsed = sample_elapsed
    if elapsed != duration_seconds:
        raise ValueError("soak_continuous")
    return value


def run_chunk(command: list[str], seconds: int, output: Path) -> dict:
    env = {
        **os.environ,
        "ROBOPARK_SOAK_DURATION_SECONDS": str(seconds),
        "ROBOPARK_SOAK_OUTPUT": str(output),
    }
    output.unlink(missing_ok=True)
    result = subprocess.run(command, check=False, env=env)
    if result.returncode != 0 or not output.is_file() or output.is_symlink():
        raise ValueError("soak_chunk")
    return _validate_sample(json.loads(output.read_text()))


def run_continuous(
    command: list[str], *, duration_seconds: int, output: Path, run_token: str
) -> dict:
    """Run the producer once so samples share one browser and one context."""
    env = {
        **os.environ,
        "ROBOPARK_SOAK_DURATION_SECONDS": str(duration_seconds),
        "ROBOPARK_SOAK_OUTPUT": str(output),
        "ROBOPARK_SOAK_RUN_TOKEN": run_token,
    }
    output.unlink(missing_ok=True)
    began = time.monotonic()
    result = subprocess.run(command, check=False, env=env)
    real_elapsed = time.monotonic() - began
    if result.returncode != 0 or not output.is_file() or output.is_symlink():
        raise ValueError("soak_continuous")
    validated = validate_continuous_report(
        json.loads(output.read_text()),
        duration_seconds=duration_seconds,
        run_token=run_token,
    )
    if real_elapsed + 1 < duration_seconds:
        raise ValueError("soak_duration_not_real")
    return {**validated, "producer_wall_seconds": real_elapsed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--source-tree-sha256", required=True)
    parser.add_argument("--duration-seconds", type=int, default=28_800)
    parser.add_argument("--chunk-seconds", type=int, default=300)
    parser.add_argument("--warmup-samples", type=int, default=2)
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="require one long-running producer with in-process samples",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if not args.command:
            raise ValueError("soak_command")
        if args.continuous:
            if args.checkpoint.exists() or args.report.exists():
                raise ValueError("soak_continuous")
            run_token = uuid.uuid4().hex
            continuous = run_continuous(
                args.command,
                duration_seconds=args.duration_seconds,
                output=args.checkpoint,
                run_token=run_token,
            )
            samples = continuous["samples"]
            evaluation = evaluate_samples(samples, warmup_samples=args.warmup_samples)
            report = {
                "format": 2,
                "passed": (
                    args.duration_seconds >= 28_800
                    and continuous["elapsed_seconds"] >= 28_800
                    and evaluation["passed"]
                ),
                "target_seconds": args.duration_seconds,
                "elapsed_seconds": continuous["elapsed_seconds"],
                "producer_wall_seconds": continuous["producer_wall_seconds"],
                "source_tree_sha256": args.source_tree_sha256,
                "continuous_contexts": continuous["continuous_contexts"],
                "evaluation": evaluation,
                "samples": samples,
            }
            write_checkpoint(args.report, report)
            return 0 if report["passed"] else 2
        if args.checkpoint.exists():
            state = read_checkpoint(args.checkpoint)
            expected = (
                args.source_tree_sha256,
                args.duration_seconds,
                args.chunk_seconds,
            )
            actual = (
                state["source_tree_sha256"],
                state["target_seconds"],
                state["chunk_seconds"],
            )
            if actual != expected:
                raise ValueError("checkpoint_config")
        else:
            state = new_checkpoint(
                source_tree_sha256=args.source_tree_sha256,
                duration_seconds=args.duration_seconds,
                chunk_seconds=args.chunk_seconds,
            )
            write_checkpoint(args.checkpoint, state)
        while state["elapsed_seconds"] < state["target_seconds"]:
            remaining = state["target_seconds"] - state["elapsed_seconds"]
            chunk = min(state["chunk_seconds"], remaining)
            began = time.monotonic()
            sample_path = args.checkpoint.with_suffix(".sample.json")
            sample = run_chunk(args.command, chunk, sample_path)
            real_elapsed = time.monotonic() - began
            if real_elapsed + 1 < chunk:
                raise ValueError("soak_duration_not_real")
            state["elapsed_seconds"] += chunk
            state["samples"].append(
                {**sample, "elapsed_seconds": state["elapsed_seconds"]}
            )
            write_checkpoint(args.checkpoint, state)
        evaluation = evaluate_samples(
            state["samples"], warmup_samples=args.warmup_samples
        )
        report = {
            "format": 1,
            "passed": state["elapsed_seconds"] >= 28_800 and evaluation["passed"],
            "target_seconds": state["target_seconds"],
            "elapsed_seconds": state["elapsed_seconds"],
            "source_tree_sha256": state["source_tree_sha256"],
            "evaluation": evaluation,
            "samples": state["samples"],
        }
        write_checkpoint(args.report, report)
        return 0 if report["passed"] else 2
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        print("cache_soak_failed", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
