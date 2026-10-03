"""Host clock helpers: Europe/Moscow wall time + optional NTP/timezone sync."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from paths import ROOT

MSK = ZoneInfo("Europe/Moscow")
_SYNC_SCRIPT = ROOT / "deploy" / "lib" / "sync_moscow_time.sh"
_LAST_SYNC_ATTEMPT = 0.0
_SYNC_COOLDOWN_SEC = 3600.0


def msk_now() -> datetime:
    return datetime.now(MSK)


def msk_now_label() -> str:
    return msk_now().strftime("%Y-%m-%d %H:%M:%S %Z")


def host_tz_name() -> str:
    """Best-effort local timezone name from the OS (may be wrong on misconfigured hosts)."""
    try:
        link = Path("/etc/localtime")
        if link.is_symlink():
            target = os.path.realpath(link)
            if "zoneinfo/" in target:
                return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        return Path("/etc/timezone").read_text(encoding="utf-8").strip() or "?"
    except OSError:
        return os.environ.get("TZ") or "?"


def clock_report() -> dict[str, str | int | bool]:
    """Snapshot for admin status / logs."""
    now = msk_now()
    host = host_tz_name()
    env_tz = os.environ.get("TZ") or ""
    return {
        "msk_now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "msk_offset": now.strftime("%z"),
        "host_tz": host,
        "env_tz": env_tz,
        "host_is_moscow": host in ("Europe/Moscow", "MSK", "Moscow") or host.endswith("/Moscow"),
    }


def try_sync_moscow_time(*, force: bool = False) -> tuple[bool, str]:
    """Run deploy/lib/sync_moscow_time.sh via sudo -n (passwordless). Cooldown 1h."""
    global _LAST_SYNC_ATTEMPT
    now = time.time()
    if not force and (now - _LAST_SYNC_ATTEMPT) < _SYNC_COOLDOWN_SEC:
        return False, "cooldown"
    _LAST_SYNC_ATTEMPT = now
    script = _SYNC_SCRIPT
    if not script.is_file():
        return False, "missing sync_moscow_time.sh"
    try:
        proc = subprocess.run(
            ["sudo", "-n", str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception as e:
        return False, str(e)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        return True, out[-500:] if out else "ok"
    return False, out[-500:] if out else f"exit {proc.returncode}"
