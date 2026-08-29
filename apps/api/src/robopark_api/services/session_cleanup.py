"""Periodic removal of expired authentication sessions.

Expired rows in ``sessions`` were never deleted, so the table grew for the
lifetime of the installation. Cleanup also runs opportunistically on login;
this loop covers instances where nobody logs in for a long time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from robopark_api.db import SessionLocal
from robopark_api.routers.auth import purge_expired_sessions

logger = logging.getLogger(__name__)


def purge_expired_sessions_once() -> int:
    with SessionLocal() as db:
        removed = purge_expired_sessions(db)
    if removed:
        logger.info("Purged %s expired session(s)", removed)
    return removed


async def run_session_cleanup_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 3600.0,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(purge_expired_sessions_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Session cleanup cycle failed")

        if stop_event.is_set():
            break
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
