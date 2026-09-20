from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

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


def read_health_alerts(settings) -> list[tuple[str, str, str]]:
    if not settings.ops_host_root:
        return []
    try:
        return health_alerts(host_bridge.system_health(host_bridge.host_root(settings)))
    except (host_bridge.BridgeError, OSError):
        logger.warning("System notification health source is unavailable", exc_info=True)
        return []


async def run_system_notification_loop(
    stop_event: asyncio.Event,
    *,
    settings,
    emit: Callable[..., dict],
    interval_seconds: float = 60.0,
) -> None:
    active: set[str] = set()
    while not stop_event.is_set():
        alerts = await asyncio.to_thread(read_health_alerts, settings)
        current = {f"system:{signature}" for _, signature, _ in alerts}
        emit_owner = getattr(emit, "__self__", None)
        sync_incidents = getattr(emit_owner, "sync_system_incidents", None)
        if callable(sync_incidents):
            try:
                await asyncio.to_thread(sync_incidents, current)
            except Exception:
                logger.exception("System incident lifecycle update failed")
        for event_type, signature, text in alerts:
            incident_key = f"system:{signature}"
            if incident_key in active:
                continue
            try:
                await asyncio.to_thread(
                    emit,
                    event_type=event_type,
                    park_id=None,
                    protected_text=text,
                    event_key=incident_key,
                )
            except Exception:
                logger.exception("System notification delivery failed")
        active = current
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
