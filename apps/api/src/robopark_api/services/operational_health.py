"""Bounded, non-sensitive host observations shared by local API workers."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from robopark_api.db import engine
from robopark_api.services.cache_metrics import snapshot_all

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
_rss_samples: deque[int] = deque(maxlen=12)


def _directory_size(root: Path, *, max_entries: int = 10_000) -> int | None:
    """Measure one explicit category without following directory symlinks."""
    try:
        if root.is_symlink() or not root.is_dir():
            return None
        total = 0
        seen = 0
        pending = [root]
        while pending:
            current = pending.pop()
            for entry in os.scandir(current):
                seen += 1
                if seen > max_entries:
                    return None
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += info.st_size
        return total
    except OSError:
        return None


def _rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _read_json(path: Path) -> dict:
    try:
        if path.is_symlink() or path.stat().st_size > 65_536:
            return {}
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def process_observations(
    *, category_roots: dict[str, Path], cleanup_state: Path | None = None
) -> dict:
    rss = _rss_bytes()
    if rss is not None:
        _rss_samples.append(rss)
    metrics = snapshot_all()
    cache_bytes = sum(
        int(value.get("bytes", 0))
        for value in metrics.values()
        if isinstance(value, dict) and isinstance(value.get("bytes", 0), int | float)
    )
    try:
        checked_out = engine.pool.checkedout()
    except (AttributeError, TypeError):
        checked_out = None
    try:
        open_fds = len(list(Path("/proc/self/fd").iterdir()))
    except OSError:
        open_fds = None
    try:
        tasks = len(list(Path("/proc/self/task").iterdir()))
    except OSError:
        tasks = threading.active_count()
    directory_bytes = {
        name: amount
        for name, root in category_roots.items()
        if (amount := _directory_size(root)) is not None
    }
    from robopark_api.services.cache_cleanup import memory_pressure_status

    return {
        "rss_bytes": rss,
        "rss_trend_bytes": (_rss_samples[-1] - _rss_samples[0]) if len(_rss_samples) > 1 else 0,
        "open_fds": open_fds,
        "tasks": tasks,
        "threads": threading.active_count(),
        "cache_bytes": cache_bytes,
        "db_pool_checked_out": checked_out,
        "directory_bytes": directory_bytes,
        "last_cleanup": _read_json(cleanup_state) if cleanup_state is not None else {},
        "memory_pressure": memory_pressure_status(),
    }


def _capabilities(public_health: dict) -> dict:
    value = public_health.get("capabilities", {})
    return {
        "profile": value.get("profile", "generic-arm"),
        "jpeg_backend": value.get("jpeg_backend", "software"),
        "hardware_jpeg": value.get("hardware_jpeg") is True,
        "npu_available": value.get("npu_available") is True,
        "cuda_available": value.get("cuda_available") is True,
        "nvme_available": value.get("nvme_available") is True,
    }


def _cpu_health() -> dict:
    """Load average is a bounded local read, not a blocking CPU probe."""
    try:
        load_1m = os.getloadavg()[0]
        cores = os.cpu_count()
    except (OSError, ValueError):
        return {"state": "unknown", "load_1m": None, "cores": None}
    return {
        "state": "ok" if cores else "unknown",
        "load_1m": load_1m,
        "cores": cores,
    }


def _public_service_health(public_health: dict, name: str) -> dict:
    try:
        checked_at = datetime.fromisoformat(public_health["services_checked_at"])
        if checked_at.tzinfo is None:
            return {"state": "unknown"}
        age = time.time() - checked_at.astimezone(UTC).timestamp()
        if age < -60 or age > 35 * 60:
            return {"state": "unknown"}
    except (KeyError, TypeError, ValueError):
        return {"state": "unknown"}
    services = public_health.get("services", {})
    if not isinstance(services, dict):
        return {"state": "unknown"}
    value = services.get(name)
    if value == "ok":
        return {"state": "ok"}
    if value in {"degraded", "failed", "warning"}:
        return {"state": "degraded"}
    return {"state": "unknown"}


def cached_host_snapshot(
    data_dir: Path, ops_dir: Path, public_health_path: Path | None = None
) -> dict:
    public_health_path = public_health_path or ops_dir / "host-health.json"
    key = str(data_dir) + ":" + str(ops_dir) + ":" + str(public_health_path)
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
        public_health = _read_json(public_health_path)
        from robopark_api.services.live_merge import default_live_merge_root
        from robopark_api.services.report_attachments import attachments_root
        from robopark_api.services.task_timeline import staged_attachments_root

        result = {
            "sampled_at": now,
            "window_seconds": WINDOW_SECONDS,
            "disk": disk,
            "memory": read_memory(),
            "backup": backup_status(ops_dir, now=now),
            "requests": read_observations(ops_dir / "observations", now=now),
            "process": process_observations(
                category_roots={
                    "live_merge": default_live_merge_root(),
                    "report_attachments": attachments_root(),
                    "tracker_uploads": staged_attachments_root(),
                },
                cleanup_state=None,
            ),
            "capabilities": _capabilities(public_health),
            "cpu": _cpu_health(),
            "postgresql": {"state": "unknown"},
            "container": _public_service_health(public_health, "docker"),
            "tuna": _public_service_health(public_health, "tuna"),
            "internet": _public_service_health(public_health, "internet"),
        }
        cleanup = public_health.get("storage", {})
        api_cleanup = _read_json(ops_dir / "api-storage-retention.json")
        result["process"]["last_cleanup"] = cleanup
        floor = max(6 * 1024**3, int((disk["total_bytes"] or 0) * 0.15))
        free = disk["free_bytes"] or 0
        result["storage"] = {
            "floor_bytes": floor,
            "bytes_to_reclaim": max(0, floor - free),
            "category_bytes": cleanup.get("category_bytes", result["process"]["directory_bytes"]),
            "last_cleanup_at": cleanup.get("completed_at"),
            "cleanup_failed": cleanup.get("blocked") is True or cleanup.get("pressure") is True,
            "api_last_cleanup_at": api_cleanup.get("completed_at"),
            "api_cleanup_failed": api_cleanup.get("pressure") is True,
            "api_cleanup_owners": api_cleanup.get("owners", {}),
        }
        _snapshot_cache.clear()
        _snapshot_cache[key] = (now, result)
        return result
