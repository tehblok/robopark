"""Host disk cleanup and leak metrics. Never touches secrets or location/role JSON."""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from paths import DATA_DIR, LOGS_DIR

STAMP_FILE = DATA_DIR / ".housekeep_last"

LOG_MAX_BYTES = 8 * 1024 * 1024
LOG_KEEP_BYTES = 512 * 1024
ARCHIVE_KEEP_DAYS = 14
ARCHIVE_KEEP_NEWEST = 7
OTA_TREE_MAX_AGE_SEC = 36 * 3600
EXPORT_ZIPS_KEEP = 3
PNG_TABLES_MAX_AGE_SEC = 48 * 3600
KILLSWITCH_PNG_MAX_AGE_SEC = 7 * 24 * 3600
DEFAULT_INTERVAL_SEC = 3600

PROTECTED_NAMES = frozenset(
    {
        "secrets.env",
        "locations.json",
        "locations.sidecar.json",
        "roles.json",
        "roles.sidecar.json",
        "dispatcher_users.json",
        "dispatcher_users.sidecar.json",
        "dispatcher_pending.json",
        "dispatcher_registration_state.json",
        "dispatcher_admin_state.json",
        "broadcasts.json",
        "schedules.json",
        "sk_campaigns.json",
        "latest_report.csv",
        "cleaned_report.csv",
        "release.zip",
        "last_result.json",
    }
)

LOCK_FILES = (".send_running", ".reminders_running")

RSS_WARN_MB = 350.0
FD_WARN = 256
LOGS_WARN_MB = 80.0
OTA_WARN_MB = 200.0


def cap_mapping(mapping: dict, max_size: int) -> int:
    """Drop oldest keys so len(mapping) <= max_size. Returns how many were removed."""
    extra = len(mapping) - int(max_size)
    if extra <= 0 or max_size < 0:
        return 0
    removed = 0
    for key in list(mapping.keys())[:extra]:
        mapping.pop(key, None)
        removed += 1
    return removed


def rotate_log(
    path: Path,
    *,
    max_bytes: int | None = None,
    keep_bytes: int | None = None,
) -> bool:
    """If log is over max_bytes, keep only the tail. Returns True if rewritten."""
    max_bytes = LOG_MAX_BYTES if max_bytes is None else max_bytes
    keep_bytes = LOG_KEEP_BYTES if keep_bytes is None else keep_bytes
    if not path.is_file() or path.name in PROTECTED_NAMES:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= max_bytes:
        return False
    keep = min(keep_bytes, size)
    try:
        with path.open("rb") as f:
            f.seek(max(0, size - keep))
            tail = f.read()
    except OSError:
        return False
    nl = tail.find(b"\n")
    if 0 <= nl < len(tail) - 1:
        tail = tail[nl + 1 :]
    header = (
        f"# truncated {datetime.now().isoformat(timespec='seconds')} "
        f"(was {size} bytes)\n"
    ).encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(header + tail)
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def prune_old_files(
    paths: Iterable[Path],
    *,
    max_age_sec: float,
    keep_newest: int = 0,
    protected_names: frozenset[str] | set[str] | None = None,
) -> int:
    protected = PROTECTED_NAMES if protected_names is None else frozenset(protected_names)
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_file() and path.name not in protected:
            files.append(path)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    now = time.time()
    removed = 0
    for i, path in enumerate(files):
        if i < keep_newest:
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age < max_age_sec:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def prune_stale_locks() -> int:
    removed = 0
    for name in LOCK_FILES:
        path = DATA_DIR / name
        if not path.is_file():
            continue
        try:
            pid = int((path.read_text(encoding="utf-8") or "0").strip() or "0")
        except (OSError, ValueError):
            pid = 0
        if _pid_alive(pid):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _prune_ota_trees() -> int:
    ota = DATA_DIR / "ota"
    removed = 0
    now = time.time()
    for name in ("staging", "backup_tree"):
        path = ota / name
        if not path.is_dir():
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age < OTA_TREE_MAX_AGE_SEC:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    return removed


_SKIP_DIR_NAMES = frozenset({"__pycache__", ".git"})
_DISK_CACHE: tuple[tuple[str, str], dict, float] | None = None
DISK_CACHE_TTL_SEC = 60.0
DIR_SIZE_MAX_FILES = 2000


def dir_size_bytes(path: Path, *, max_files: int = DIR_SIZE_MAX_FILES) -> int:
    """Bounded walk (no symlinks). Caps files so a fat OTA staging cannot hitch the CPU."""
    if not path.exists():
        return 0
    total = 0
    seen = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if seen >= max_files:
                        return total
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in _SKIP_DIR_NAMES:
                                stack.append(Path(entry.path))
                            continue
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            seen += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _mb(num: int | float) -> float:
    return round(float(num) / (1024 * 1024), 2)


def disk_usage(*, force: bool = False) -> dict:
    global _DISK_CACHE
    now = time.time()
    key = (str(DATA_DIR), str(LOGS_DIR))
    if not force and _DISK_CACHE is not None:
        cached_key, data, ts = _DISK_CACHE
        if cached_key == key and now - ts < DISK_CACHE_TTL_SEC:
            return dict(data)
    data = {
        "logs_mb": _mb(dir_size_bytes(LOGS_DIR)),
        "ota_mb": _mb(dir_size_bytes(DATA_DIR / "ota")),
        "archive_mb": _mb(dir_size_bytes(DATA_DIR / "archive")),
        "png_mb": _mb(
            dir_size_bytes(DATA_DIR / "location_tables")
            + dir_size_bytes(DATA_DIR / "killswitch_progress")
            + dir_size_bytes(DATA_DIR / "sk_progress")
        ),
    }
    _DISK_CACHE = (key, dict(data), now)
    return data


def leak_warnings(metrics: dict, disk: dict | None = None) -> list[str]:
    disk = disk or {}
    warnings: list[str] = []
    rss = float(metrics.get("rss_mb") or 0)
    fds = int(metrics.get("fd_count") or 0)
    logs_mb = float(disk.get("logs_mb") or 0)
    ota_mb = float(disk.get("ota_mb") or 0)
    if rss >= RSS_WARN_MB:
        warnings.append(f"RSS {rss} МБ")
    if fds >= FD_WARN:
        warnings.append(f"открыто FD: {fds}")
    if logs_mb >= LOGS_WARN_MB:
        warnings.append(f"логи {logs_mb} МБ")
    if ota_mb >= OTA_WARN_MB:
        warnings.append(f"OTA {ota_mb} МБ")
    return warnings


def leak_report(*, refresh_disk: bool = False) -> dict:
    from store.runtime import process_metrics

    metrics = process_metrics()
    disk = disk_usage(force=refresh_disk)
    return {
        **metrics,
        **disk,
        "warnings": leak_warnings(metrics, disk),
    }


def run_housekeep() -> dict:
    """Clean logs, old archives, OTA leftovers, stale locks. Never secrets/locations."""
    result = {
        "logs_rotated": 0,
        "archive_pruned": 0,
        "ota_zips_pruned": 0,
        "ota_trees_pruned": 0,
        "png_pruned": 0,
        "locks_pruned": 0,
        "usage_days_pruned": 0,
        "removed": 0,
    }
    if LOGS_DIR.is_dir():
        for log_path in sorted(LOGS_DIR.glob("*.log")):
            if rotate_log(log_path):
                result["logs_rotated"] += 1
    archive = DATA_DIR / "archive"
    if archive.is_dir():
        result["archive_pruned"] = prune_old_files(
            archive.glob("snapshot_*.csv"),
            max_age_sec=ARCHIVE_KEEP_DAYS * 24 * 3600,
            keep_newest=ARCHIVE_KEEP_NEWEST,
        )
    ota = DATA_DIR / "ota"
    if ota.is_dir():
        result["ota_zips_pruned"] = prune_old_files(
            ota.glob("working-bot-export-*.zip"),
            max_age_sec=0,
            keep_newest=EXPORT_ZIPS_KEEP,
        )
        result["ota_trees_pruned"] = _prune_ota_trees()
    tables = DATA_DIR / "location_tables"
    if tables.is_dir():
        result["png_pruned"] += prune_old_files(
            tables.glob("*.png"),
            max_age_sec=PNG_TABLES_MAX_AGE_SEC,
        )
    ks = DATA_DIR / "killswitch_progress"
    if ks.is_dir():
        result["png_pruned"] += prune_old_files(
            ks.glob("*.png"),
            max_age_sec=KILLSWITCH_PNG_MAX_AGE_SEC,
        )
    sk_dir = DATA_DIR / "sk_progress"
    if sk_dir.is_dir():
        result["png_pruned"] += prune_old_files(
            sk_dir.rglob("*.png"),
            max_age_sec=KILLSWITCH_PNG_MAX_AGE_SEC,
        )
    result["locks_pruned"] = prune_stale_locks()
    try:
        from dispatcher_stats import prune_old_daily

        result["usage_days_pruned"] = prune_old_daily()
    except Exception:
        pass
    result["removed"] = (
        result["logs_rotated"]
        + result["archive_pruned"]
        + result["ota_zips_pruned"]
        + result["ota_trees_pruned"]
        + result["png_pruned"]
        + result["locks_pruned"]
        + result["usage_days_pruned"]
    )
    result["summary"] = (
        f"логи {result['logs_rotated']}, архив {result['archive_pruned']}, "
        f"OTA {result['ota_zips_pruned']}+{result['ota_trees_pruned']}, "
        f"PNG {result['png_pruned']}, замки {result['locks_pruned']}"
    )
    return result


def maybe_run(*, force: bool = False, min_interval_sec: int = DEFAULT_INTERVAL_SEC) -> dict:
    """Run at most once per interval unless force=True."""
    stamp = Path(STAMP_FILE)
    if not force and stamp.is_file():
        try:
            age = time.time() - stamp.stat().st_mtime
        except OSError:
            age = min_interval_sec + 1
        if age < min_interval_sec:
            return {"ran": False, "removed": 0, "reason": "interval"}
    result = run_housekeep()
    result["ran"] = True
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")
    except OSError:
        pass
    disk_usage(force=True)
    return result


def format_disk_report(*, leak: dict | None = None, result: dict | None = None) -> str:
    leak = leak if leak is not None else leak_report(refresh_disk=bool(result))
    lines = [
        "<b>Диск и память</b>",
        f"RSS: {leak.get('rss_mb', 0)} МБ · FD: {leak.get('fd_count', 0)}",
        f"Логи: {leak.get('logs_mb', 0)} МБ · OTA: {leak.get('ota_mb', 0)} МБ",
        f"Архив CSV: {leak.get('archive_mb', 0)} МБ · PNG: {leak.get('png_mb', 0)} МБ",
    ]
    warns = leak.get("warnings") or []
    if warns:
        lines.append("⚠️ " + "; ".join(str(w) for w in warns))
    else:
        lines.append("Утечек по порогам нет.")
    stamp = Path(STAMP_FILE)
    if stamp.is_file():
        try:
            when = stamp.read_text(encoding="utf-8").strip() or "—"
        except OSError:
            when = "—"
        lines.append(f"Последняя автоочистка: <code>{when}</code>")
    else:
        lines.append("Автоочистка ещё не запускалась.")
    if result and result.get("ran"):
        lines.append("Очистка: " + str(result.get("summary") or "готово"))
    return "\n".join(lines)
