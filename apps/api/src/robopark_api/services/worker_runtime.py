"""Exclusive, cooperatively stopped background-job runtime."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from robopark_api.services import tracker_client
from robopark_api.services.blocker_history_job import run_blocker_history_loop
from robopark_api.services.bootstrap import initialize_data
from robopark_api.services.cache_cleanup import run_cache_cleanup_loop
from robopark_api.services.campaigns import run_refresh_loop as run_campaign_refresh_loop
from robopark_api.services.database_locks import dispose_database_lock_engines
from robopark_api.services.emergency_keepalive import run_keepalive_loop
from robopark_api.services.live_merge import JobLease, default_live_merge_root
from robopark_api.services.notification_delivery import run_notification_delivery_loop
from robopark_api.services.ops.maintenance import host_maintenance_active
from robopark_api.services.session_cleanup import run_session_cleanup_loop
from robopark_api.services.sync_health import record_worker_heartbeat, release_worker_heartbeat
from robopark_api.services.system_notifications import run_system_notification_loop
from robopark_api.services.system_observability import run_metric_collection_loop
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


async def _heartbeat_loop(session_factory: Any, stop: asyncio.Event, owner_id: str) -> None:
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=15)
        if not stop.is_set():
            await asyncio.to_thread(_write_heartbeat, session_factory, owner_id)


def _write_heartbeat(session_factory: Any, owner_id: str) -> None:
    with session_factory() as db:
        record_worker_heartbeat(db, owner_id=owner_id)


class WorkerRuntime:
    def __init__(self, settings: Any, session_factory: Any, push_service: Any) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.push_service = push_service

    async def start(self, stop: asyncio.Event) -> None:
        """Run background jobs under one cross-process lease until shutdown."""
        global _health
        lease = JobLease(default_live_merge_root(), "lifespan-jobs")
        tasks: list[asyncio.Task[None]] = []
        owns_lease = False
        owner_id = str(uuid4())
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

            _write_heartbeat(self.session_factory, owner_id)

            tasks = [
                asyncio.create_task(_heartbeat_loop(self.session_factory, stop, owner_id)),
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
                    run_metric_collection_loop(self.session_factory, stop, settings=self.settings)
                ),
                asyncio.create_task(
                    run_notification_delivery_loop(self.session_factory, stop, owner_id=owner_id)
                ),
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
            from robopark_api.services.ai import jobs as ai_jobs

            tasks.append(
                asyncio.create_task(ai_jobs.run_loop(self.session_factory, stop, self.settings))
            )
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
                        try:
                            with self.session_factory() as db:
                                release_worker_heartbeat(db, owner_id=owner_id)
                        finally:
                            lease.release()
