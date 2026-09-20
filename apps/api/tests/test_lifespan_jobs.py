"""Short lifecycle contracts for the single-owner background-job boundary."""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from robopark_api import main


def test_lifespan_holds_jobs_at_maintenance_barrier_and_releases_lease_after_shutdown(
    db_engine, test_settings, monkeypatch
):
    """No writer starts before maintenance ends; all stop before the lease releases."""
    maintenance_active = threading.Event()
    maintenance_active.set()
    lease_attempted = threading.Event()
    worker_started = threading.Event()
    worker_stopped = threading.Event()
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
            assert worker_stopped.is_set()
            lease_released.set()

    async def bounded_idle(stop_event, **_kwargs):
        worker_started.set()
        try:
            await stop_event.wait()
        finally:
            worker_stopped.set()

    async def bounded_idle_with_factory(_session_factory, stop_event, **_kwargs):
        await bounded_idle(stop_event)

    for name in (
        "run_keepalive_loop",
        "run_blocker_history_loop",
        "run_session_cleanup_loop",
        "run_cache_cleanup_loop",
        "run_system_notification_loop",
    ):
        monkeypatch.setattr(main, name, bounded_idle)
    for name in (
        "run_tracker_outbox_loop",
        "run_campaign_refresh_loop",
        "run_tracker_notification_loop",
    ):
        monkeypatch.setattr(main, name, bounded_idle_with_factory)

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
    assert not worker_started.is_set()

    maintenance_active.clear()
    assert lease_attempted.wait(timeout=1)
    assert worker_started.wait(timeout=1)
    close_context.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert worker_stopped.is_set()
    assert lease_released.is_set()
