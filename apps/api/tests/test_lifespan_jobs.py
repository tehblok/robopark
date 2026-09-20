"""Short lifecycle contracts for the single-owner background-job boundary."""

from __future__ import annotations

import asyncio
import threading

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from robopark_api import main


def test_lifespan_collects_every_worker_before_releasing_shared_resources(
    db_engine, test_settings, monkeypatch
):
    """A failed sibling must not detach a real writer thread during shutdown."""
    entered = threading.Event()
    close_context = threading.Event()
    writer_started = threading.Event()
    finish_writer = threading.Event()
    writer_stopped = threading.Event()
    push_closed = threading.Event()
    lock_engines_disposed = threading.Event()
    lease_released = threading.Event()

    class Lease:
        def __init__(self, root, name):
            del root, name

        def try_acquire(self):
            return True

        def release(self):
            assert writer_stopped.is_set()
            assert push_closed.is_set()
            assert lock_engines_disposed.is_set()
            lease_released.set()

    async def failing_loop(stop_event, **_kwargs):
        await asyncio.to_thread(writer_started.wait)
        raise RuntimeError("sibling failed")

    async def blocking_writer_loop(stop_event, **_kwargs):
        def write_until_stopped():
            writer_started.set()
            finish_writer.wait(timeout=2)
            writer_stopped.set()

        await asyncio.to_thread(write_until_stopped)
        assert stop_event.is_set()

    async def idle_loop(stop_event, **_kwargs):
        await stop_event.wait()

    async def idle_factory_loop(_session_factory, stop_event, **_kwargs):
        await stop_event.wait()

    monkeypatch.setattr(main, "run_keepalive_loop", failing_loop)
    monkeypatch.setattr(main, "run_blocker_history_loop", blocking_writer_loop)
    monkeypatch.setattr(main, "run_session_cleanup_loop", idle_loop)
    monkeypatch.setattr(main, "run_cache_cleanup_loop", idle_loop)
    monkeypatch.setattr(main, "run_system_notification_loop", idle_loop)
    monkeypatch.setattr(main, "run_tracker_outbox_loop", idle_factory_loop)
    monkeypatch.setattr(main, "run_campaign_refresh_loop", idle_factory_loop)
    monkeypatch.setattr(main, "run_tracker_notification_loop", idle_factory_loop)

    class PushService:
        def __init__(self, _session_factory):
            pass

        def emit(self, **_kwargs):
            return {"event_id": "test"}

        def close(self):
            assert writer_stopped.is_set()
            push_closed.set()

    monkeypatch.setattr(main.push, "PushService", PushService)

    def dispose_lock_engines():
        assert writer_stopped.is_set()
        lock_engines_disposed.set()

    monkeypatch.setattr(main, "dispose_database_lock_engines", dispose_lock_engines)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(main, "live_merge_enabled", lambda: True)
    monkeypatch.setattr(main, "JobLease", Lease)

    def serve():
        with TestClient(main.create_app()):
            entered.set()
            close_context.wait(timeout=2)

    thread = threading.Thread(target=serve)
    thread.start()
    assert entered.wait(timeout=1)
    assert writer_started.wait(timeout=1)
    close_context.set()
    try:
        assert not lease_released.wait(timeout=0.1)
        assert not push_closed.is_set()
        assert not lock_engines_disposed.is_set()
    finally:
        finish_writer.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert writer_stopped.is_set()
    assert push_closed.is_set()
    assert lock_engines_disposed.is_set()
    assert lease_released.is_set()


def test_lifespan_holds_jobs_at_maintenance_barrier_and_releases_lease_after_shutdown(
    db_engine, test_settings, monkeypatch
):
    """No writer starts before maintenance ends; all stop before the lease releases."""
    maintenance_active = threading.Event()
    maintenance_active.set()
    lease_attempted = threading.Event()
    worker_names = (
        "keepalive",
        "blocker_history",
        "session_cleanup",
        "cache_cleanup",
        "system_notification",
        "tracker_outbox",
        "campaign_refresh",
        "tracker_notification",
    )
    worker_started = {name: threading.Event() for name in worker_names}
    worker_stopped = {name: threading.Event() for name in worker_names}
    push_closed = threading.Event()
    lock_engines_disposed = threading.Event()
    lease_released = threading.Event()
    entered = threading.Event()
    close_context = threading.Event()

    class Lease:
        def __init__(self, root, name):
            del root
            assert name == "lifespan-jobs"

        def try_acquire(self):
            lease_attempted.set()
            return True

        def release(self):
            assert all(event.is_set() for event in worker_stopped.values())
            assert push_closed.is_set()
            assert lock_engines_disposed.is_set()
            lease_released.set()

    def bounded_idle(name):
        async def run(stop_event, **_kwargs):
            worker_started[name].set()
            try:
                await stop_event.wait()
            finally:
                worker_stopped[name].set()

        return run

    def bounded_idle_with_factory(name):
        async def run(_session_factory, stop_event, **_kwargs):
            worker_started[name].set()
            try:
                await stop_event.wait()
            finally:
                worker_stopped[name].set()

        return run

    monkeypatch.setattr(main, "run_keepalive_loop", bounded_idle("keepalive"))
    monkeypatch.setattr(main, "run_blocker_history_loop", bounded_idle("blocker_history"))
    monkeypatch.setattr(main, "run_session_cleanup_loop", bounded_idle("session_cleanup"))
    monkeypatch.setattr(main, "run_cache_cleanup_loop", bounded_idle("cache_cleanup"))
    monkeypatch.setattr(main, "run_system_notification_loop", bounded_idle("system_notification"))
    monkeypatch.setattr(
        main, "run_tracker_outbox_loop", bounded_idle_with_factory("tracker_outbox")
    )
    monkeypatch.setattr(
        main, "run_campaign_refresh_loop", bounded_idle_with_factory("campaign_refresh")
    )
    monkeypatch.setattr(
        main,
        "run_tracker_notification_loop",
        bounded_idle_with_factory("tracker_notification"),
    )

    class PushService:
        def __init__(self, _session_factory):
            pass

        def emit(self, **_kwargs):
            return {"event_id": "test"}

        def close(self):
            assert all(event.is_set() for event in worker_stopped.values())
            push_closed.set()

    monkeypatch.setattr(main.push, "PushService", PushService)

    def dispose_lock_engines():
        assert all(event.is_set() for event in worker_stopped.values())
        lock_engines_disposed.set()

    monkeypatch.setattr(main, "dispose_database_lock_engines", dispose_lock_engines)

    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(
        main, "host_maintenance_active", lambda _settings: maintenance_active.is_set()
    )
    monkeypatch.setattr(main, "live_merge_enabled", lambda: True)
    monkeypatch.setattr(main, "JobLease", Lease)

    def serve():
        with TestClient(main.create_app()):
            entered.set()
            close_context.wait(timeout=1)

    thread = threading.Thread(target=serve)
    thread.start()
    assert entered.wait(timeout=1)
    assert not lease_attempted.wait(timeout=0.1)
    assert not any(event.is_set() for event in worker_started.values())

    maintenance_active.clear()
    assert lease_attempted.wait(timeout=1)
    assert all(event.wait(timeout=1) for event in worker_started.values())
    close_context.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert all(event.is_set() for event in worker_stopped.values())
    assert push_closed.is_set()
    assert lock_engines_disposed.is_set()
    assert lease_released.is_set()
