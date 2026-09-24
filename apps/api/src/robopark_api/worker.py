"""Dedicated background worker process entry point."""

from __future__ import annotations

import asyncio
import logging
import signal

from robopark_api.config import get_settings
from robopark_api.db import SessionLocal
from robopark_api.routers.push import PushService
from robopark_api.services.worker_runtime import WorkerRuntime


async def run() -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    settings = get_settings()
    bind = getattr(SessionLocal, "kw", {}).get("bind")
    if bind is not None:
        bind._robopark_ops_settings = settings
    await WorkerRuntime(settings, SessionLocal, PushService(lambda: SessionLocal())).start(stop)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
