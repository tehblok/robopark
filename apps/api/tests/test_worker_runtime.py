"""Exclusive ownership and cooperative shutdown of all background loops."""

import asyncio
import threading

import pytest
from sqlalchemy.orm import sessionmaker

from robopark_api.services import worker_runtime


def test_worker_starts_each_loop_once_and_joins_before_releasing_lease(
    tmp_path, db_engine, test_settings, monkeypatch
):
    names = (
        "keepalive",
        "blocker_history",
        "session_cleanup",
        "cache_cleanup",
        "system_notification",
        "tracker_outbox",
        "campaign_refresh",
        "tracker_notification",
    )
    started = []
    stopped = []

    def loop(name):
        async def run(*args, **kwargs):
            stop = next(arg for arg in args if isinstance(arg, asyncio.Event))
            started.append(name)
            try:
                await stop.wait()
            finally:
                stopped.append(name)

        return run

    for name in names:
        attribute = {
            "campaign_refresh": "run_campaign_refresh_loop",
        }.get(name, f"run_{name}_loop")
        monkeypatch.setattr(worker_runtime, attribute, loop(name))
    monkeypatch.setattr(worker_runtime, "default_live_merge_root", lambda: tmp_path)
    monkeypatch.setattr(worker_runtime, "host_maintenance_active", lambda _settings: False)

    class Push:
        def emit(self, **kwargs):
            return {}

        def close(self):
            assert set(stopped) == set(names)

    async def scenario():
        stop = asyncio.Event()
        factory = sessionmaker(bind=db_engine, future=True)
        first = worker_runtime.WorkerRuntime(test_settings, factory, Push())
        task = asyncio.create_task(first.start(stop))
        for _ in range(100):
            if len(started) == len(names):
                break
            await asyncio.sleep(0.01)
        assert sorted(started) == sorted(names)

        second_stop = asyncio.Event()
        second = worker_runtime.WorkerRuntime(test_settings, factory, Push())
        second_task = asyncio.create_task(second.start(second_stop))
        await asyncio.sleep(0.05)
        assert len(started) == len(names)
        assert worker_runtime.worker_health().running

        second_stop.set()
        await asyncio.wait_for(second_task, timeout=2)
        stop.set()
        await asyncio.wait_for(task, timeout=2)
        assert sorted(stopped) == sorted(names)
        assert not worker_runtime.worker_health().running

    asyncio.run(scenario())


def test_failed_job_joins_inflight_writer_before_shared_cleanup(
    db_engine, test_settings, monkeypatch
):
    writer_started = threading.Event()
    finish_writer = threading.Event()
    writer_stopped = threading.Event()
    push_closed = threading.Event()
    locks_disposed = threading.Event()
    lease_released = threading.Event()

    class Lease:
        def __init__(self, root, name):
            del root, name

        def try_acquire(self):
            return True

        def release(self):
            assert writer_stopped.is_set()
            assert push_closed.is_set()
            assert locks_disposed.is_set()
            lease_released.set()

    async def failed(stop, **kwargs):
        await asyncio.to_thread(writer_started.wait)
        raise RuntimeError("failed job")

    async def writing(stop, **kwargs):
        def complete():
            writer_started.set()
            finish_writer.wait(timeout=2)
            writer_stopped.set()

        await asyncio.to_thread(complete)
        assert stop.is_set()

    async def idle(*args, **kwargs):
        stop = next(arg for arg in args if isinstance(arg, asyncio.Event))
        await stop.wait()

    monkeypatch.setattr(worker_runtime, "JobLease", Lease)
    monkeypatch.setattr(worker_runtime, "host_maintenance_active", lambda _settings: False)
    monkeypatch.setattr(worker_runtime, "run_keepalive_loop", failed)
    monkeypatch.setattr(worker_runtime, "run_blocker_history_loop", writing)
    for name in (
        "run_session_cleanup_loop",
        "run_cache_cleanup_loop",
        "run_system_notification_loop",
        "run_tracker_outbox_loop",
        "run_campaign_refresh_loop",
        "run_tracker_notification_loop",
    ):
        monkeypatch.setattr(worker_runtime, name, idle)
    monkeypatch.setattr(
        worker_runtime,
        "dispose_database_lock_engines",
        lambda: locks_disposed.set() if writer_stopped.is_set() else pytest.fail("early DB close"),
    )

    class Push:
        def emit(self, **kwargs):
            return {}

        def close(self):
            assert writer_stopped.is_set()
            push_closed.set()

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(
            worker_runtime.WorkerRuntime(
                test_settings, sessionmaker(bind=db_engine, future=True), Push()
            ).start(stop)
        )
        assert await asyncio.to_thread(writer_started.wait, 1)
        await asyncio.sleep(0.05)
        assert not push_closed.is_set()
        assert not lease_released.is_set()
        finish_writer.set()
        with pytest.raises(RuntimeError, match="background worker failed"):
            await asyncio.wait_for(task, timeout=2)
        assert writer_stopped.is_set()
        assert push_closed.is_set()
        assert locks_disposed.is_set()
        assert lease_released.is_set()

    asyncio.run(scenario())


def test_worker_defers_initialization_until_maintenance_ends(db_engine, test_settings, monkeypatch):
    maintenance = asyncio.Event()
    maintenance.set()
    initialized = []
    attempts = []

    class Lease:
        def __init__(self, root, name):
            del root, name

        def try_acquire(self):
            attempts.append(True)
            return True

        def release(self):
            pass

    async def idle(*args, **kwargs):
        stop = next(arg for arg in args if isinstance(arg, asyncio.Event))
        await stop.wait()

    for name in (
        "run_keepalive_loop",
        "run_blocker_history_loop",
        "run_session_cleanup_loop",
        "run_cache_cleanup_loop",
        "run_system_notification_loop",
        "run_tracker_outbox_loop",
        "run_campaign_refresh_loop",
        "run_tracker_notification_loop",
    ):
        monkeypatch.setattr(worker_runtime, name, idle)
    monkeypatch.setattr(worker_runtime, "JobLease", Lease)
    monkeypatch.setattr(
        worker_runtime, "host_maintenance_active", lambda _settings: maintenance.is_set()
    )
    monkeypatch.setattr(
        worker_runtime,
        "initialize_data",
        lambda _session_factory, _settings: initialized.append(True),
        raising=False,
    )

    class Push:
        def emit(self, **kwargs):
            return {}

        def close(self):
            pass

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(
            worker_runtime.WorkerRuntime(
                test_settings, sessionmaker(bind=db_engine, future=True), Push()
            ).start(stop)
        )
        await asyncio.sleep(0.05)
        assert not attempts
        assert not initialized
        maintenance.clear()
        for _ in range(100):
            if initialized:
                break
            await asyncio.sleep(0.01)
        assert initialized == [True]
        assert attempts == [True]
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
