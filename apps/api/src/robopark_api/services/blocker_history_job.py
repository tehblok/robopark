"""Background blocker history scans every two hours."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from robopark_api.db import SessionLocal
from robopark_api.services import tracker_history
from robopark_api.services.analytics_history import scan_all_parks_once as scan_analytics_once
from robopark_api.services.blocker_history import (
    BUCKET_SECONDS,
    align_bucket_start,
    scan_all_parks_once,
)
from robopark_api.services.ops.maintenance import host_maintenance_active

logger = logging.getLogger(__name__)

PENDING_SLA_INTERVAL_SECONDS = 15.0


def seconds_until_next_bucket(now: datetime | None = None) -> float:
    """Seconds until the current 2h bucket ends (even-hour grid)."""
    now_utc = now or datetime.now(UTC)
    now_utc = now_utc.replace(tzinfo=UTC) if now_utc.tzinfo is None else now_utc.astimezone(UTC)
    bucket_start = align_bucket_start(now_utc)
    bucket_end = bucket_start + timedelta(seconds=BUCKET_SECONDS)
    remaining = (bucket_end - now_utc).total_seconds()
    return max(0.0, remaining)


async def run_blocker_history_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float | None = None,
    pending_interval_seconds: float = PENDING_SLA_INTERVAL_SECONDS,
) -> None:
    """Run heavy history scans and the bounded pending-SLA drain until shutdown."""
    if pending_interval_seconds <= 0:
        raise ValueError("pending_interval_seconds_must_be_positive")

    loop = asyncio.get_running_loop()
    next_full_scan = loop.time()
    next_pending_scan = loop.time()
    while not stop_event.is_set():
        now = loop.time()
        if now >= next_full_scan:
            try:
                await _run_sync_scan(_scan_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Blocker history scan cycle failed")
            if stop_event.is_set():
                break
            delay = (
                interval_seconds if interval_seconds is not None else seconds_until_next_bucket()
            )
            next_full_scan = loop.time() + max(0.0, delay)

        now = loop.time()
        if now >= next_pending_scan:
            try:
                await _run_sync_scan(_scan_pending_sla_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Pending SLA history scan failed")
            next_pending_scan = loop.time() + pending_interval_seconds
            if stop_event.is_set():
                break

        delay = max(0.0, min(next_full_scan, next_pending_scan) - loop.time())
        if delay == 0:
            # Preserve the explicit zero full-scan interval used by focused tests,
            # while still yielding to shutdown and the other worker loops.
            await asyncio.sleep(0)
            continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)


async def _run_sync_scan(scan) -> None:
    """Do not release the worker lease while a cooperative scan thread is active."""
    task = asyncio.create_task(asyncio.to_thread(scan))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def _scan_once() -> None:
    if host_maintenance_active():
        return
    with SessionLocal() as db:
        try:
            scan_all_parks_once(db)
        finally:
            # A flow-counter failure must not erase the opportunity to observe state.
            db.rollback()
            scan_analytics_once(db)


def _scan_pending_sla_once() -> None:
    if host_maintenance_active():
        return
    with SessionLocal() as db:
        tracker_history.drain_pending_history(db, limit=2)
