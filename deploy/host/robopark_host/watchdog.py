"""Bounded local-readiness watchdog with a consecutive failure threshold."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .checks import Runner, execute
from .paths import HostPaths
from .repair import DEFAULT_REPAIRS
from .state import HostBusy, atomic_write_json, host_operation

FAILURE_THRESHOLD = 3


@dataclass(frozen=True)
class WatchdogResult:
    consecutive_failures: int
    restarted: str | None
    busy: bool = False


def _ready(http: Any) -> bool:
    try:
        response = getattr(http, "get", http)(
            "http://127.0.0.1:8080/api/health/ready", timeout=5
        )
        payload = response.json() if callable(getattr(response, "json", None)) else {}
        return (
            int(getattr(response, "status", getattr(response, "status_code", 200))) < 400
            and payload.get("status") == "ready"
            and payload.get("checks", {}).get("database") == "ok"
        )
    except Exception:
        return False


def _previous_failures(paths: HostPaths) -> int:
    state = paths.state / "watchdog.json"
    try:
        return max(0, int(json.loads(state.read_text(encoding="utf-8")).get("consecutive_failures", 0)))
    except (OSError, ValueError, TypeError):
        return 0


def run_watchdog(paths: HostPaths, runner: Runner, http: Any) -> WatchdogResult:
    """Restart Robopark once at the third consecutive local readiness failure."""

    try:
        with host_operation(paths):
            return _watchdog_owned(paths, runner, http)
    except HostBusy:
        return WatchdogResult(_previous_failures(paths), None, busy=True)


def _watchdog_owned(paths, runner, http):
    if _ready(http):
        atomic_write_json(paths.state / "watchdog.json", {"consecutive_failures": 0})
        return WatchdogResult(0, None)
    failures = _previous_failures(paths) + 1
    restarted = None
    if failures == FAILURE_THRESHOLD:
        action = "restart_app"
        if execute(runner, DEFAULT_REPAIRS[action]).ok:
            restarted = action
    atomic_write_json(paths.state / "watchdog.json", {"consecutive_failures": failures})
    return WatchdogResult(failures, restarted)
