"""Runtime pause flags and health snapshot."""

from __future__ import annotations

import os
import resource
import sys
from pathlib import Path

from store import DATA_DIR, ROOT
from store.shared_flags import write_optional_flag

DISPATCHER_PAUSE = DATA_DIR / ".dispatcher_paused"
SEND_PAUSE = DATA_DIR / ".send_paused"
PID_FILE = DATA_DIR / "dispatcher_bot.pid"


def is_dispatcher_paused() -> bool:
    return DISPATCHER_PAUSE.exists()


def is_send_paused() -> bool:
    return SEND_PAUSE.exists()


def set_dispatcher_paused(paused: bool) -> None:
    write_optional_flag(DISPATCHER_PAUSE, b"1\n" if paused else None)


def set_send_paused(paused: bool) -> None:
    write_optional_flag(SEND_PAUSE, b"1\n" if paused else None)


def read_pid() -> int | None:
    if not PID_FILE.is_file():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def process_metrics(pid: int | None = None) -> dict:
    """RSS (MB) and open FD count. Linux uses /proc; macOS uses rusage + /dev/fd."""
    self_pid = os.getpid()
    target = int(pid) if pid else self_pid
    rss_mb = 0.0
    fd_count = 0
    status = Path(f"/proc/{target}/status")
    if status.is_file():
        try:
            for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("VmRSS:"):
                    rss_mb = round(int(line.split()[1]) / 1024, 2)
                    break
        except (OSError, ValueError, IndexError):
            rss_mb = 0.0
        fd_dir = Path(f"/proc/{target}/fd")
        if fd_dir.is_dir():
            try:
                fd_count = len(os.listdir(fd_dir))
            except OSError:
                fd_count = 0
    elif target == self_pid:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            rss_mb = round(rss / (1024 * 1024), 2)
        else:
            rss_mb = round(rss / 1024, 2)
        for candidate in (Path("/dev/fd"), Path("/proc/self/fd")):
            if candidate.is_dir():
                try:
                    fd_count = len(os.listdir(candidate))
                    break
                except OSError:
                    continue
    return {"rss_mb": rss_mb, "fd_count": int(fd_count)}


def health_snapshot() -> dict:
    from telegram_chats import resolve_profile

    pid = read_pid()
    alive = False
    if pid is not None:
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
    metrics = process_metrics(pid if alive else None)
    warnings: list[str] = []
    try:
        from store.housekeep import leak_warnings

        warnings = leak_warnings(metrics)
    except Exception:
        pass
    clock: dict = {}
    try:
        from store.clock_sync import clock_report

        clock = clock_report()
        if not clock.get("host_is_moscow"):
            warnings.append(f"host TZ={clock.get('host_tz')} (ожидается Europe/Moscow)")
    except Exception:
        pass
    return {
        "profile": resolve_profile(),
        "dispatcher_paused": is_dispatcher_paused(),
        "send_paused": is_send_paused(),
        "pid": pid,
        "pid_alive": alive,
        "root": str(ROOT),
        "rss_mb": metrics.get("rss_mb", 0),
        "fd_count": metrics.get("fd_count", 0),
        "warnings": warnings,
        "msk_now": clock.get("msk_now"),
        "host_tz": clock.get("host_tz"),
    }
