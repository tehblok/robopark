"""Exclusive, cooperatively stopped background-job runtime."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from robopark_api.services import tracker_client
from robopark_api.services.blocker_history_job import run_blocker_history_loop
from robopark_api.services.bootstrap import initialize_data
from robopark_api.services.cache_cleanup import run_cache_cleanup_loop
from robopark_api.services.campaigns import run_refresh_loop as run_campaign_refresh_loop
from robopark_api.services.database_locks import dispose_database_lock_engines
from robopark_api.services.emergency_keepalive import run_keepalive_loop
from robopark_api.services.live_merge import JobLease, default_live_merge_root
from robopark_api.services.ops.maintenance import host_maintenance_active
from robopark_api.services.session_cleanup import run_session_cleanup_loop
from robopark_api.services.system_notifications import run_system_notification_loop
from robopark_api.services.tracker_notifications import run_tracker_notification_loop
from robopark_api.services.tracker_outbox import run_tracker_outbox_loop

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerHealth:
    """Local worker state; API-visible freshness is a separate shared contract."""

    running: bool
    active_jobs: int


_health = WorkerHealth(running=False, active_jobs=0)


def worker_health() -> WorkerHealth:
    return _health


class WorkerRuntime:
    def __init__(self, settings: Any, session_factory: Any, push_service: Any) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.push_service = push_service

    async def start(self, stop: asyncio.Event) -> None:
        """Run the eight jobs under one cross-process lease until shutdown."""
        global _health
        lease = JobLease(default_live_merge_root(), "lifespan-jobs")
        tasks: list[asyncio.Task[None]] = []
        owns_lease = False
        deferred_initialization = host_maintenance_active(self.settings)
        try:
            while not stop.is_set():
                if not host_maintenance_active(self.settings) and lease.try_acquire():
                    owns_lease = True
                    break
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=0.5)
            if not owns_lease or stop.is_set():
                return

            if deferred_initialization:
                initialize_data(self.session_factory, self.settings)

            tasks = [
                asyncio.create_task(run_keepalive_loop(stop)),
                asyncio.create_task(run_blocker_history_loop(stop)),
                asyncio.create_task(
                    run_session_cleanup_loop(
                        stop,
                        interval_seconds=self.settings.session_cleanup_interval_seconds,
                    )
                ),
                asyncio.create_task(run_cache_cleanup_loop(stop)),
                asyncio.create_task(
                    run_system_notification_loop(
                        stop, settings=self.settings, emit=self.push_service.emit
                    )
                ),
                asyncio.create_task(run_tracker_outbox_loop(self.session_factory, stop)),
                asyncio.create_task(run_campaign_refresh_loop(self.session_factory, stop)),
                asyncio.create_task(
                    run_tracker_notification_loop(
                        self.session_factory,
                        stop,
                        emit=self.push_service.emit,
                        interval_seconds=self.settings.tracker_notification_interval_seconds,
                        page_size=self.settings.tracker_notification_page_size,
                        lease_seconds=self.settings.tracker_notification_lease_seconds,
                        poll_deadline_seconds=self.settings.tracker_notification_poll_deadline_seconds,
                        max_operation_seconds=max(
                            tracker_client.SEARCH_OPERATION_TIMEOUT_SECONDS,
                            self.settings.push_delivery_deadline_seconds,
                        ),
                    )
                ),
            ]
            _health = WorkerHealth(running=True, active_jobs=len(tasks))
            stop_waiter = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    [stop_waiter, *tasks], return_when=asyncio.FIRST_COMPLETED
                )
                if stop_waiter not in done:
                    stop.set()
            finally:
                stop_waiter.cancel()
                await asyncio.gather(stop_waiter, return_exceptions=True)
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(
                    result, asyncio.CancelledError
                ):
                    logger.error("background worker failed", exc_info=result)
                    raise RuntimeError("background worker failed") from result
        finally:
            # Never cancel to_thread waiters: their threads could still write after
            # releasing the lease or closing the delivery and DB lock pools.
            if tasks:
                stop.set()
                await asyncio.gather(*tasks, return_exceptions=True)
            if owns_lease:
                _health = WorkerHealth(running=False, active_jobs=0)
                try:
                    await asyncio.to_thread(self.push_service.close)
                finally:
                    try:
                        await asyncio.to_thread(dispose_database_lock_engines)
                    finally:
                        lease.release()
