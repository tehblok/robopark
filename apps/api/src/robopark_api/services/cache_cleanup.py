"""Lease-owned cleanup for bounded shared cache state."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from robopark_api.db import SessionLocal
from robopark_api.services.diagnostic_unknowns import prune_diagnostic_unknowns
from robopark_api.services.live_merge import get_live_merge_store
from robopark_api.services.ops.maintenance import host_maintenance_active

logger = logging.getLogger(__name__)


def prune_cache_once(*, now: datetime | None = None) -> tuple[int, int]:
    if host_maintenance_active():
        return 0, 0
    current = now or datetime.now(UTC)
    store = get_live_merge_store()
    files_removed = store.prune(now=current.timestamp()) if store is not None else 0
    with SessionLocal() as db:
        unknowns_removed = prune_diagnostic_unknowns(db, now=current)
    if files_removed or unknowns_removed:
        logger.info(
            "Pruned %s live-merge file(s) and %s diagnostic unknown(s)",
            files_removed,
            unknowns_removed,
        )
    return files_removed, unknowns_removed


async def run_cache_cleanup_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 3600.0,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(prune_cache_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cache cleanup cycle failed")

        if stop_event.is_set():
            break
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
