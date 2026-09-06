"""Bounded, non-sensitive host observations shared by local API workers."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

FAMILIES = ("tracker", "diagnostics", "reports")
WINDOW_SECONDS = 300


class RequestObservations:
    def __init__(self, root: Path, *, worker: str | None = None):
        self.root = root
        self.worker = worker or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.buckets: dict[int, dict] = {}
        self.lock = threading.Lock()
        self.flush_lock = threading.Lock()
        self.last_flush = 0.0

    def record(
        self, family: str, duration_ms: float, status: int, *, now: float | None = None
    ) -> bool:
        if family not in FAMILIES:
            return False
        now = time.time() if now is None else now
        with self.lock:
            self.buckets = {
                stamp: rows for stamp, rows in self.buckets.items() if stamp > now - WINDOW_SECONDS
            }
            bucket = self.buckets.setdefault(int(now // 10) * 10, {})
            row = bucket.setdefault(
                family, {"requests": 0, "errors": 0, "limited": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            row["requests"] += 1
            row["errors"] += int(status >= 500 or status == 429)
            row["limited"] += int(status == 429)
            row["total_ms"] += max(0, duration_ms)
            row["max_ms"] = max(row["max_ms"], max(0, duration_ms))
            if now - self.last_flush < 10:
                return False
            self.last_flush = now
            return True

    def flush(self, *, now: float | None = None):
        with self.flush_lock:
            self._flush(now=now)

    def _flush(self, *, now: float | None = None):
        now = time.time() if now is None else now
        with self.lock:
            payload = json.dumps({"updated_at": now, "buckets": self.buckets})
        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.root / f"{self.worker}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(payload)
            temporary.chmod(0o600)
            temporary.replace(path)
            for old in self.root.glob("*.json"):
                if old.stat().st_mtime < now - WINDOW_SECONDS * 2:
                    old.unlink(missing_ok=True)
        except OSError:
            # Observability must never fail an application request.
            pass


def read_observations(root: Path, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    result = {
        name: {"requests": 0, "errors": 0, "limited": 0, "total_ms": 0.0, "max_ms": 0.0}
        for name in FAMILIES
    }
    try:
        paths = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[
            :32
        ]
    except OSError:
        paths = []
    for path in paths:
        try:
            if path.stat().st_size > 100_000:
                continue
            payload = json.loads(path.read_text())
            for stamp, rows in payload["buckets"].items():
                if not now - WINDOW_SECONDS < int(stamp) <= now:
                    continue
                for family in FAMILIES:
                    row = rows.get(family, {})
                    for key in ("requests", "errors", "limited", "total_ms"):
                        result[family][key] += max(0, float(row.get(key, 0)))
                    result[family]["max_ms"] = max(
                        result[family]["max_ms"], float(row.get("max_ms", 0))
                    )
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    for row in result.values():
        total_ms = row.pop("total_ms")
        row["average_ms"] = round(total_ms / row["requests"], 1) if row["requests"] else None
        row["max_ms"] = round(row["max_ms"], 1) if row["requests"] else None
    return result


def read_memory() -> dict:
    result = {
        "total_bytes": None,
        "available_bytes": None,
        "container_limit_bytes": None,
        "container_used_bytes": None,
    }
    try:
        fields = {
            line.split(":", 1)[0]: int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith(("MemTotal:", "MemAvailable:"))
        }
        result["total_bytes"] = fields.get("MemTotal")
        result["available_bytes"] = fields.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    for field, filename in (
        ("container_limit_bytes", "memory.max"),
        ("container_used_bytes", "memory.current"),
    ):
        try:
            raw = (Path("/sys/fs/cgroup") / filename).read_text().strip()
            result[field] = int(raw) if raw != "max" else None
        except (OSError, ValueError):
            pass
    return result


def backup_status(ops_dir: Path, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    verified = None
    failed = False
    try:
        value = json.loads((ops_dir / "scheduled-copy.json").read_text()).get("verified_at")
        if isinstance(value, int | float) and 0 < value <= now:
            verified = value
    except (OSError, ValueError, AttributeError):
        pass
    try:
        job = json.loads((ops_dir / "job.json").read_text())
        failed = job.get("kind") == "snapshot" and job.get("state") == "failed"
    except (OSError, ValueError, AttributeError):
        pass
    return {
        "verified_at": verified,
        "last_attempt_failed": failed,
        "overdue": verified is not None and now - verified > 36 * 3600,
    }


_snapshot_lock = threading.Lock()
_snapshot_cache: dict[str, tuple[float, dict]] = {}


def cached_host_snapshot(data_dir: Path, ops_dir: Path) -> dict:
    key = str(data_dir) + ":" + str(ops_dir)
    now = time.time()
    with _snapshot_lock:
        cached = _snapshot_cache.get(key)
        if cached and now - cached[0] < 10:
            return cached[1]
        disk = {"total_bytes": None, "free_bytes": None}
        try:
            usage = shutil.disk_usage(data_dir)
            disk = {"total_bytes": usage.total, "free_bytes": usage.free}
        except OSError:
            pass
        result = {
            "sampled_at": now,
            "window_seconds": WINDOW_SECONDS,
            "disk": disk,
            "memory": read_memory(),
            "backup": backup_status(ops_dir, now=now),
            "requests": read_observations(ops_dir / "observations", now=now),
        }
        _snapshot_cache.clear()
        _snapshot_cache[key] = (now, result)
        return result
