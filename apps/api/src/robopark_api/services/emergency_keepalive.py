"""Background liveness checks for the Emergency API cookie."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from robopark_api.db import SessionLocal
from robopark_api.services import emergency_client
from robopark_api.services import platform_settings as settings_svc

MIN_INTERVAL_SECONDS = 90.0
MAX_INTERVAL_SECONDS = 120.0
INTER_VIN_GAP_SECONDS = 0.9

logger = logging.getLogger(__name__)


def keepalive_once(db: Session) -> None:
    """Run one keep-alive cycle for the recent VIN ring or optional seed VIN."""
    cookie = settings_svc.get_emergency_cookie(db)
    if not cookie:
        return

    vins = settings_svc.get_keepalive_ring(db)
    if not vins:
        seed = settings_svc.get_setting(
            db, settings_svc.EMERGENCY_KEEPALIVE_SEED_VIN_KEY
        )
        if seed is not None and seed.value.strip():
            vins = [seed.value.strip()]

    for index, vin in enumerate(vins):
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
                datetime.now(timezone.utc).isoformat(),
            )

        if index + 1 < len(vins):
            time.sleep(INTER_VIN_GAP_SECONDS)


async def run_keepalive_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float | None = None,
) -> None:
    """Run keep-alive cycles until shutdown, with an injectable test interval."""
    while not stop_event.is_set():
        try:
            with SessionLocal() as db:
                await asyncio.to_thread(keepalive_once, db)
        except Exception:
            logger.exception("Emergency keep-alive cycle failed")

        delay = (
            interval_seconds
            if interval_seconds is not None
            else random.uniform(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass
