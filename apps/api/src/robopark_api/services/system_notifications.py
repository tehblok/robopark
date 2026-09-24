from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from robopark_api.ops_schemas import SystemHealthOut
from robopark_api.services.ops import host_bridge

logger = logging.getLogger(__name__)

_DISK_CHECKS = {"resources", "inode_space", "log_growth"}
_ANOMALY_CHECKS = {"memory_load_swap", "load", "temperature"}
_INTEGRATION_CHECKS = {"integrations", "dns", "outbound_https", "tuna_inactive", "tuna_route"}


def health_alerts(health: SystemHealthOut) -> list[tuple[str, str, str]]:
    alerts: list[tuple[str, str, str]] = []
    for check in health.checks:
        if check.status == "ok":
            continue
        event_type = (
            "disk_low"
            if check.code in _DISK_CHECKS
            else "anomaly"
            if check.code in _ANOMALY_CHECKS
            else "integration_down"
            if check.code in _INTEGRATION_CHECKS
            else "problem"
        )
        alerts.append((event_type, f"{check.code}:{check.status}", check.message))
    if health.update.state == "rolled_back" or health.update.publication == "degraded":
        alerts.append(("update_failure", "update:degraded", "Обновление требует внимания"))
    if health.overall == "degraded" and not alerts:
        alerts.append(("server_problem", "system:degraded", "Система требует внимания"))
    return alerts


def read_health_alerts(settings) -> list[tuple[str, str, str]] | None:
    if not settings.ops_host_root:
        return None
    try:
        return health_alerts(host_bridge.system_health(host_bridge.host_root(settings)))
    except (host_bridge.BridgeError, OSError):
        logger.warning("System notification health source is unavailable", exc_info=True)
        return None


async def run_system_notification_loop(
    stop_event: asyncio.Event,
    *,
    settings,
    emit: Callable[..., dict],
    interval_seconds: float = 60.0,
) -> None:
    active: set[str] = set()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="system-notifications")
    loop = asyncio.get_running_loop()
    try:
        while not stop_event.is_set():
            alerts = await loop.run_in_executor(executor, read_health_alerts, settings)
            if alerts is not None:
                current = {f"system:{signature}" for _, signature, _ in alerts}
                confirmed = active & current
                emit_owner = getattr(emit, "__self__", None)
                sync_incidents = getattr(emit_owner, "sync_system_incidents", None)
                if callable(sync_incidents):
                    try:
                        await loop.run_in_executor(executor, sync_incidents, current)
                    except Exception:
                        logger.exception("System incident lifecycle update failed")
                for event_type, signature, text in alerts:
                    incident_key = f"system:{signature}"
                    if incident_key in active:
                        continue
                    try:
                        operation = partial(
                            emit,
                            event_type=event_type,
                            park_id=None,
                            protected_text=text,
                            event_key=incident_key,
                        )
                        await loop.run_in_executor(executor, operation)
                    except Exception:
                        logger.exception("System notification persistence failed")
                    else:
                        confirmed.add(incident_key)
                # A failed first emission must remain eligible for the next poll.
                # Stable occurrence/event keys make that retry idempotent even
                # when the event and its delivery rows were already committed.
                active = confirmed
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
