"""Background liveness checks for the Emergency API cookie."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import threading
import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from robopark_api.db import SessionLocal
from robopark_api.services import emergency_client, reports
from robopark_api.services import platform_settings as settings_svc

MIN_INTERVAL_SECONDS = 90.0
MAX_INTERVAL_SECONDS = 120.0
INTER_VIN_GAP_SECONDS = 0.9

logger = logging.getLogger(__name__)


def _interruptible_sleep(seconds: float, stop_event: threading.Event | None) -> bool:
    """Sleep up to *seconds*. Return False if *stop_event* was set."""
    if stop_event is None:
        time.sleep(seconds)
        return True

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if stop_event.is_set():
            return False
        time.sleep(min(0.1, deadline - time.monotonic()))
    return not stop_event.is_set()


def keepalive_once(
    db: Session | None = None,
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """Run one keep-alive cycle for the recent VIN ring or optional seed VIN."""
    if stop_event is not None and stop_event.is_set():
        return

    if db is not None:
        _keepalive_once_with_db(db, stop_event)
        return

    with SessionLocal() as session:
        _keepalive_once_with_db(session, stop_event)


def _keepalive_once_with_db(db: Session, stop_event: threading.Event | None) -> None:
    cookie = settings_svc.get_emergency_cookie(db)
    if not cookie:
        return

    vins = settings_svc.get_keepalive_ring(db)
    if not vins:
        seed = settings_svc.get_setting(db, settings_svc.EMERGENCY_KEEPALIVE_SEED_VIN_KEY)
        if seed is not None and seed.value.strip():
            vins = [seed.value.strip()]

    for index, vin in enumerate(vins):
        if stop_event is not None and stop_event.is_set():
            return

        try:
            emergency_client.fetch_robot_payload(cookie=cookie, vin=vin)
        except emergency_client.EmergencyAuthError:
            settings_svc.set_emergency_cookie_valid(db, False)
            return
        except emergency_client.EmergencyError:
            logger.warning("Emergency keep-alive failed for VIN %s", vin)
        else:
            settings_svc.set_emergency_cookie_valid(db, True)
            settings_svc.set_setting(
                db,
                settings_svc.EMERGENCY_KEEPALIVE_LAST_OK_KEY,
                datetime.now(UTC).isoformat(),
            )
            reports.resolve_open_emergency_cookie_reports(db)

        if index + 1 < len(vins) and not _interruptible_sleep(INTER_VIN_GAP_SECONDS, stop_event):
            return


async def run_keepalive_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float | None = None,
) -> None:
    """Run keep-alive cycles until shutdown, with an injectable test interval."""
    thread_stop = threading.Event()
    try:
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(keepalive_once, stop_event=thread_stop)
            except asyncio.CancelledError:
                thread_stop.set()
                raise
            except Exception:
                logger.exception("Emergency keep-alive cycle failed")

            if stop_event.is_set():
                break

            delay = (
                interval_seconds
                if interval_seconds is not None
                else random.uniform(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
    finally:
        thread_stop.set()
