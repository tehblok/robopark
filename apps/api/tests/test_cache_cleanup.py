import asyncio
import threading
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from robopark_api import main
from robopark_api.services import cache_cleanup


def test_cleanup_once_prunes_live_merge_and_diagnostic_unknowns(monkeypatch):
    now = datetime(2026, 9, 14, tzinfo=UTC)
    db = object()
    calls = []

    class SessionContext:
        def __enter__(self):
            return db

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class Store:
        def prune(self, **kwargs):
            calls.append(("files", kwargs))
            return 4

    monkeypatch.setattr(cache_cleanup, "SessionLocal", SessionContext)
    monkeypatch.setattr(cache_cleanup, "get_live_merge_store", lambda: Store())
    monkeypatch.setattr(
        cache_cleanup,
        "prune_diagnostic_unknowns",
        lambda session, **kwargs: calls.append(("unknowns", session, kwargs)) or 3,
    )

    assert cache_cleanup.prune_cache_once(now=now) == (4, 3)
    assert calls == [
        ("files", {"now": now.timestamp()}),
        ("unknowns", db, {"now": now}),
    ]


def test_cleanup_loop_runs_without_sleeping_and_stops_on_event(monkeypatch):
    called = threading.Event()

    def fake_cleanup_once():
        called.set()
        return (0, 0)

    monkeypatch.setattr(cache_cleanup, "prune_cache_once", fake_cleanup_once)

    async def exercise():
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            cache_cleanup.run_cache_cleanup_loop(stop_event, interval_seconds=3600)
        )
        assert await asyncio.to_thread(called.wait, 1)
        stop_event.set()
        await task

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("merge_enabled", "won_lease"),
    [(True, False), (True, True), (False, False), (False, True)],
)
def test_lifespan_starts_cleanup_only_for_job_lease_owner(
    db_engine, test_settings, monkeypatch, merge_enabled, won_lease
):
    lease_attempted = threading.Event()
    cleanup_started = threading.Event()

    class Lease:
        def __init__(self, root, name):
            assert name == "lifespan-jobs"

        def try_acquire(self):
            lease_attempted.set()
            return won_lease

        def release(self):
            return None

    async def idle_loop(stop_event, **kwargs):
        await stop_event.wait()

    async def cleanup_loop(stop_event, **kwargs):
        cleanup_started.set()
        await stop_event.wait()

    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(main, "live_merge_enabled", lambda: merge_enabled)
    monkeypatch.setattr(main, "JobLease", Lease)
    monkeypatch.setattr(main, "run_keepalive_loop", idle_loop)
    monkeypatch.setattr(main, "run_blocker_history_loop", idle_loop)
    monkeypatch.setattr(main, "run_session_cleanup_loop", idle_loop)
    monkeypatch.setattr(main, "run_cache_cleanup_loop", cleanup_loop)

    with TestClient(main.create_app()):
        assert lease_attempted.wait(timeout=1)
        if won_lease:
            assert cleanup_started.wait(timeout=1)
        else:
            assert not cleanup_started.is_set()

    assert cleanup_started.is_set() is won_lease
