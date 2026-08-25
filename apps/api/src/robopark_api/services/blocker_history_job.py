"""Background blocker history scans every two hours."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from robopark_api.db import SessionLocal
from robopark_api.services.blocker_history import (
    BUCKET_SECONDS,
    align_bucket_start,
    scan_all_parks_once,
)

logger = logging.getLogger(__name__)


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
                interval_seconds if interval_seconds is not None else seconds_until_next_bucket()
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
    finally:
        pass


def _scan_once() -> None:
    with SessionLocal() as db:
        scan_all_parks_once(db)
