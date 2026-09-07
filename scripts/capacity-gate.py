#!/usr/bin/env python3
"""200 virtual users; credentials enter only through a private JSON file.

Uses httpx from the API lockfile. Output contains aggregates, never URLs,
headers, bodies, credentials or exception text. See deploy/CAPACITY-RU.md.
"""

import argparse
import asyncio
import json
import math
import os
import re
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

READ_PATHS = ("/api/auth/me", "/api/operator/parks", "/api/reports/mine", "/api/reports/badge")
FAILURE_PATTERNS = {
    "database_lock": ("database is locked", "database table is locked", "sqlite_busy"),
    "event_loop": (
        "task exception was never retrieved",
        "event loop is closed",
        "event loop blocked",
        "slow callback",
        "executing <task",
    ),
    "oom": ("oomkilled", "out of memory", "oom-kill"),
    "restarts": ("container restart", "restart_count_increased"),
}


@dataclass(repr=False)
class Config:
    base_url: str
    session: str
    users: int = 200
    duration_seconds: float = 600
    warmup_seconds: float = 30
    think_seconds: float = 1
    writes: bool = False
    isolated_test_data: bool = False

    def public(self):
        return {
            "users": self.users,
            "duration_seconds": self.duration_seconds,
            "warmup_seconds": self.warmup_seconds,
            "think_seconds": self.think_seconds,
            "writes": self.writes,
            "read_paths": READ_PATHS,
        }


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("config_invalid")
        result[key] = value
    return result


def load_config(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid not in (0, os.geteuid())
                or info.st_size > 65536
            ):
                raise ValueError("config_permissions")
            raw = stream.read(65537)
        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict) or set(value) - set(Config.__dataclass_fields__):
            raise ValueError("config_invalid")
        config = Config(**value)
        url = urlsplit(config.base_url)
        if (
            url.scheme not in ("https", "http")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or url.port == 0
            or any(ord(c) < 33 for c in config.base_url)
            or (url.scheme == "http" and url.hostname not in ("127.0.0.1", "::1", "localhost"))
        ):
            raise ValueError("config_invalid")
        if not isinstance(config.session, str) or not re.fullmatch(
            r"[A-Za-z0-9._~-]{1,4096}", config.session
        ):
            raise ValueError("config_invalid")
        if type(config.users) is not int or not 1 <= config.users <= 1000:
            raise ValueError("config_invalid")
        for key, lower, upper in [
            ("duration_seconds", 0.001, 3600),
            ("warmup_seconds", 0, 600),
            ("think_seconds", 0, 60),
        ]:
            number = getattr(config, key)
            if (
                type(number) not in (int, float)
                or not math.isfinite(number)
                or not lower <= number <= upper
            ):
                raise ValueError("config_invalid")
        if (
            type(config.writes) is not bool
            or type(config.isolated_test_data) is not bool
            or config.writes
            and (not config.isolated_test_data or os.environ.get("ALLOW_ISOLATED_WRITES") != "true")
        ):
            raise ValueError("config_invalid")
        config.base_url = config.base_url.rstrip("/")
        return config
    except ValueError as error:
        raise ValueError(
            "config_permissions" if str(error) == "config_permissions" else "config_invalid"
        ) from None
    except (OSError, TypeError, AttributeError, UnicodeError):
        raise ValueError("config_invalid") from None


@dataclass
class Sample:
    latency_ms: float
    status: int
    failures: dict = field(default_factory=dict)


def failure_counts(text):
    lowered = text.lower()
    return {
        key: int(any(pattern in lowered for pattern in patterns))
        for key, patterns in FAILURE_PATTERNS.items()
    }


def scan_server_log(path):
    counts = dict.fromkeys(FAILURE_PATTERNS, 0)
    with Path(path).open(errors="replace") as stream:
        for line in stream:
            for key, count in failure_counts(line).items():
                counts[key] += count
    return counts


def summarize(samples, *, elapsed):
    values = sorted(sample.latency_ms for sample in samples)
    size = len(values)
    return {
        "requests": size,
        "throughput_rps": size / elapsed if elapsed > 0 else 0,
        "error_rate": sum(not 200 <= sample.status < 300 for sample in samples) / size
        if size
        else 1,
        "latency_ms": {
            name: values[math.ceil(size * quantile) - 1] if size else None
            for name, quantile in [("p50", 0.50), ("p95", 0.95), ("p99", 0.99)]
        },
    }


def evaluate_gate(metrics, *, users, duration, warmup, server_evidence):
    latency = metrics["latency_ms"]
    if (
        users != 200
        or duration < 600
        or warmup < 30
        or metrics["requests"] == 0
        or metrics["throughput_rps"] < 100
        or metrics["error_rate"] > 0.001
        or any(
            latency[name] is None or latency[name] > bound
            for name, bound in [("p50", 250), ("p95", 1000), ("p99", 2000)]
        )
    ):
        return "FAIL"
    if server_evidence is None:
        return "PENDING_SERVER_EVIDENCE"
    if set(server_evidence) != set(FAILURE_PATTERNS) or any(
        type(n) is not int or n != 0 for n in server_evidence.values()
    ):
        return "FAIL"
    return "PASS"


async def run_load(config, *, transport=None):
    samples = []
    observed = dict.fromkeys(FAILURE_PATTERNS, 0)
    isolated_park_id = None
    cleanup_ok = True
    sample_limit = False
    async with httpx.AsyncClient(
        base_url=config.base_url,
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        headers={"Cookie": "robopark_session=" + config.session},
        limits=httpx.Limits(max_connections=config.users, max_keepalive_connections=config.users),
        timeout=httpx.Timeout(10),
    ) as client:

        async def request(method, path, body=None):
            began = time.monotonic()
            try:
                async with client.stream(method, path, json=body) as response:
                    retained = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(retained) + len(chunk) > 1024 * 1024:
                            return Sample((time.monotonic() - began) * 1000, 0)
                        retained.extend(chunk)
                    return Sample(
                        (time.monotonic() - began) * 1000,
                        response.status_code,
                        failure_counts(retained.decode(errors="replace")),
                    )
            except (httpx.HTTPError, OSError):
                return Sample((time.monotonic() - began) * 1000, 0)

        try:
            if config.writes:
                tag = "capacity-" + uuid4().hex
                response = await client.post("/api/parks", json={"name": tag, "tag": tag})
                if response.status_code != 201:
                    raise ValueError("isolated_setup_failed")
                created = response.json()
                candidate_id = created.get("id") if isinstance(created, dict) else None
                if type(candidate_id) is not int or not 0 < candidate_id < 2**63:
                    raise ValueError("isolated_setup_failed")
                # Cleanup authority begins only after validation. Never interpolate
                # rejected response values into a PATCH path from the finally block.
                isolated_park_id = candidate_id
                inactive = await request(
                    "PATCH", f"/api/parks/{isolated_park_id}", {"is_active": False}
                )
                if not 200 <= inactive.status < 300:
                    raise ValueError("isolated_setup_failed")

            start = time.monotonic()
            measure_start = start + config.warmup_seconds
            deadline = measure_start + config.duration_seconds

            async def user(index):
                nonlocal sample_limit
                sequence = index
                while time.monotonic() < deadline:
                    began = time.monotonic()
                    if config.writes and sequence % 10 == 0:
                        sample = await request(
                            "PATCH",
                            f"/api/parks/{isolated_park_id}",
                            {"name": f"{tag}-{index:x}-{sequence:x}"},
                        )
                    else:
                        sample = await request("GET", READ_PATHS[sequence % len(READ_PATHS)])
                    if began >= measure_start:
                        if len(samples) >= 2_000_000:
                            sample_limit = True
                            return
                        samples.append(sample)
                        for key, number in sample.failures.items():
                            observed[key] += number
                    sequence += 1
                    # Yield even with in-memory test transport and zero think time.
                    await asyncio.sleep(
                        min(config.think_seconds, max(0, deadline - time.monotonic()))
                    )

            await asyncio.gather(*(user(index) for index in range(config.users)))
            elapsed = max(0.001, time.monotonic() - measure_start)
        finally:
            if isolated_park_id is not None:
                cleaned = await request(
                    "PATCH", f"/api/parks/{isolated_park_id}", {"is_active": False}
                )
                cleanup_ok = 200 <= cleaned.status < 300
    metrics = summarize(samples, elapsed=elapsed)
    gate = evaluate_gate(
        metrics,
        users=config.users,
        duration=config.duration_seconds,
        warmup=config.warmup_seconds,
        server_evidence=None,
    )
    if sample_limit or not cleanup_ok or any(observed.values()):
        gate = "FAIL"
    return {
        "format": 1,
        "workload": config.public(),
        "metrics": metrics,
        "elapsed_seconds": elapsed,
        "observed_failures": observed,
        "server_evidence": None,
        "isolated_park_id": isolated_park_id,
        "cleanup_ok": cleanup_ok,
        "sample_limit_reached": sample_limit,
        "gate": gate,
    }


def validate_report(value):
    """Reject arbitrary text before re-exporting an aggregate report."""

    def require(condition):
        if not condition:
            raise ValueError("report_invalid")

    def number(item):
        return type(item) in (int, float) and math.isfinite(item) and item >= 0

    require(
        isinstance(value, dict)
        and set(value)
        == {
            "format",
            "workload",
            "metrics",
            "elapsed_seconds",
            "observed_failures",
            "server_evidence",
            "isolated_park_id",
            "cleanup_ok",
            "sample_limit_reached",
            "gate",
        }
    )
    require(value["format"] == 1 and value["gate"] in ("PASS", "FAIL", "PENDING_SERVER_EVIDENCE"))
    workload = value["workload"]
    require(
        isinstance(workload, dict)
        and set(workload)
        == {"users", "duration_seconds", "warmup_seconds", "think_seconds", "writes", "read_paths"}
    )
    require(workload["read_paths"] == list(READ_PATHS) and type(workload["writes"]) is bool)
    require(
        all(
            number(workload[key])
            for key in ("users", "duration_seconds", "warmup_seconds", "think_seconds")
        )
    )
    metrics = value["metrics"]
    require(
        isinstance(metrics, dict)
        and set(metrics) == {"requests", "throughput_rps", "error_rate", "latency_ms"}
    )
    require(all(number(metrics[key]) for key in ("requests", "throughput_rps", "error_rate")))
    require(
        isinstance(metrics["latency_ms"], dict)
        and set(metrics["latency_ms"]) == {"p50", "p95", "p99"}
    )
    require(all(item is None or number(item) for item in metrics["latency_ms"].values()))
    require(number(value["elapsed_seconds"]))
    for key in ("observed_failures", "server_evidence"):
        counts = value[key]
        if key == "server_evidence" and counts is None:
            continue
        require(isinstance(counts, dict) and set(counts) == set(FAILURE_PATTERNS))
        require(all(type(item) is int and item >= 0 for item in counts.values()))
    require(all(type(value[key]) is bool for key in ("cleanup_ok", "sample_limit_reached")))
    require(
        value["isolated_park_id"] is None
        or type(value["isolated_park_id"]) is int
        and value["isolated_park_id"] > 0
    )
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--config", type=Path, help="private mode-0600 JSON; never put credentials in argv"
    )
    modes.add_argument(
        "--evaluate", type=Path, help="evaluate saved aggregate report with target logs"
    )
    parser.add_argument(
        "--server-log", type=Path, help="logs covering the entire measured target run"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.config:
            result = asyncio.run(run_load(load_config(args.config)))
        else:
            result = validate_report(
                json.loads(args.evaluate.read_text(), object_pairs_hook=unique_object)
            )
            if not args.server_log:
                raise ValueError("server_log_required")
            evidence = scan_server_log(args.server_log)
            result["server_evidence"] = evidence
            workload = result["workload"]
            result["gate"] = evaluate_gate(
                result["metrics"],
                users=workload["users"],
                duration=workload["duration_seconds"],
                warmup=workload["warmup_seconds"],
                server_evidence=evidence,
            )
            if (
                not result["cleanup_ok"]
                or result["sample_limit_reached"]
                or any(result["observed_failures"].values())
            ):
                result["gate"] = "FAIL"
        # Never overwrite the credential file or an existing report/symlink.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"gate": result["gate"], "requests": result["metrics"]["requests"]}))
        return 0 if result["gate"] == "PASS" else 2
    except (ValueError, OSError, TypeError, KeyError, httpx.HTTPError):
        print("capacity_gate_failed: check configuration, reachability or report", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
