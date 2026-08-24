"""Background blocker history scans every two hours."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from robopark_api.db import SessionLocal
from robopark_api.services.blocker_history import (
    BUCKET_SECONDS,
    align_bucket_start,
    scan_all_parks_once,
)

logger = logging.getLogger(__name__)


def seconds_until_next_bucket(now: datetime | None = None) -> float:
    """Seconds until the current 2h bucket ends (even-hour grid)."""
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    bucket_start = align_bucket_start(now_utc)
    bucket_end = bucket_start + timedelta(seconds=BUCKET_SECONDS)
    remaining = (bucket_end - now_utc).total_seconds()
    return max(0.0, remaining)


async def run_blocker_history_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float | None = None,
) -> None:
    """Run blocker history scans until shutdown."""
    try:
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(_scan_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Blocker history scan cycle failed")

            if stop_event.is_set():
                break

            delay = (
                interval_seconds
                if interval_seconds is not None
                else seconds_until_next_bucket()
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except TimeoutError:
                pass
    finally:
        pass


def _scan_once() -> None:
    with SessionLocal() as db:
        scan_all_parks_once(db)
