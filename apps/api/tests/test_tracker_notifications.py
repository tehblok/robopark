import asyncio
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from robopark_api import main
from robopark_api.schedule_models import TrackerNotificationCursor
from robopark_api.services import platform_settings, tracker_cache, tracker_client
from robopark_api.services.tracker_notifications import poll_tracker_notifications


def _issue(key: str, created: str) -> dict:
    return {
        "key": key,
        "summary": f"blocker [{key}]",
        "status": "В очереди",
        "status_key": "queued",
        "queue": "ROBOPARK",
        "created": created,
        "tags": ["Alpha"],
    }


def _factory(db_engine):
    return sessionmaker(bind=db_engine, future=True)


def _capture(events):
    return lambda **event: events.append(event)


def test_poller_emits_new_task_without_tracker_get(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_cache,
        "search_issues",
        lambda **_kwargs: [_issue("ROBOPARK-9", "2026-09-20T18:00:00Z")],
    )
    emitted = []

    processed = poll_tracker_notifications(
        _factory(db_engine), _capture(emitted), page_size=10, owner_id="worker-a"
    )

    assert processed == 1
    assert emitted == [
        {
            "event_type": "new_task",
            "park_id": seed_park_with_tracker.id,
            "protected_text": "Новая задача ROBOPARK-9",
            "event_key": "new-task:ROBOPARK-9",
        }
    ]


def test_two_workers_emit_one_event_for_the_same_issue(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    monkeypatch.setattr(
        tracker_cache,
        "search_issues",
        lambda **_kwargs: [_issue("ROBOPARK-10", "2026-09-20T18:01:00Z")],
    )
    emitted = []
    factory = _factory(db_engine)

    first = poll_tracker_notifications(factory, _capture(emitted), page_size=10, owner_id="worker-a")
    second = poll_tracker_notifications(factory, _capture(emitted), page_size=10, owner_id="worker-b")

    assert (first, second) == (1, 0)
    assert [event["event_key"] for event in emitted] == ["new-task:ROBOPARK-10"]


def test_restart_replays_cursor_boundary_without_duplicate(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    issues = [_issue("ROBOPARK-11", "2026-09-20T18:02:00Z")]
    monkeypatch.setattr(tracker_cache, "search_issues", lambda **_kwargs: list(issues))
    emitted = []
    factory = _factory(db_engine)

    assert (
        poll_tracker_notifications(factory, _capture(emitted), page_size=10, owner_id="worker-a")
        == 1
    )
    tracker_cache.clear_all()
    issues.append(_issue("ROBOPARK-12", "2026-09-20T18:03:00Z"))
    assert (
        poll_tracker_notifications(factory, _capture(emitted), page_size=10, owner_id="worker-b")
        == 1
    )

    assert [event["event_key"] for event in emitted] == [
        "new-task:ROBOPARK-11",
        "new-task:ROBOPARK-12",
    ]


def test_tracker_failure_preserves_cursor_and_next_poll_recovers(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    attempts = 0

    def search(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise tracker_client.TrackerError("token leaked? no")
        return [_issue("ROBOPARK-13", "2026-09-20T18:04:00Z")]

    monkeypatch.setattr(tracker_cache, "search_issues", search)
    emitted = []
    factory = _factory(db_engine)

    assert (
        poll_tracker_notifications(factory, _capture(emitted), page_size=10, owner_id="worker-a")
        == 0
    )
    with Session(db_engine) as db:
        failed = db.get(TrackerNotificationCursor, "new-tasks")
        assert failed is not None
        assert failed.cursor_value is None
        assert failed.last_error == "tracker_unavailable"

    assert (
        poll_tracker_notifications(factory, _capture(emitted), page_size=10, owner_id="worker-b")
        == 1
    )
    with Session(db_engine) as db:
        recovered = db.get(TrackerNotificationCursor, "new-tasks")
        assert recovered is not None
        assert recovered.cursor_value is not None
        assert recovered.last_error is None
        assert recovered.last_success_at is not None
    assert [event["event_key"] for event in emitted] == ["new-task:ROBOPARK-13"]


def _patch_idle_workers(monkeypatch):
    async def idle(stop_event, **kwargs):
        del kwargs
        await stop_event.wait()

    async def idle_with_factory(session_factory, stop_event, **kwargs):
        del session_factory, kwargs
        await stop_event.wait()

    for name in (
        "run_keepalive_loop",
        "run_blocker_history_loop",
        "run_session_cleanup_loop",
        "run_cache_cleanup_loop",
        "run_system_notification_loop",
    ):
        monkeypatch.setattr(main, name, idle)
    monkeypatch.setattr(main, "run_tracker_outbox_loop", idle_with_factory)
    monkeypatch.setattr(main, "run_campaign_refresh_loop", idle_with_factory)


@pytest.mark.parametrize("won_lease", [False, True])
def test_lifespan_starts_tracker_poller_only_for_lease_owner(
    db_engine, test_settings, monkeypatch, won_lease
):
    started = threading.Event()
    captured = {}

    class Lease:
        def __init__(self, root, name):
            del root
            assert name == "lifespan-jobs"

        def try_acquire(self):
            return won_lease

        def release(self):
            return None

    async def notification_loop(session_factory, stop_event, **kwargs):
        captured.update(kwargs)
        assert session_factory is main.SessionLocal
        started.set()
        await stop_event.wait()

    _patch_idle_workers(monkeypatch)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(main, "live_merge_enabled", lambda: True)
    monkeypatch.setattr(main, "JobLease", Lease)
    monkeypatch.setattr(main, "run_tracker_notification_loop", notification_loop, raising=False)

    with TestClient(main.create_app()):
        if won_lease:
            assert started.wait(timeout=1)
        else:
            assert not started.is_set()

    assert started.is_set() is won_lease
    if won_lease:
        assert captured["interval_seconds"] == test_settings.tracker_notification_interval_seconds
        assert captured["page_size"] == test_settings.tracker_notification_page_size
        assert captured["lease_seconds"] == test_settings.tracker_notification_lease_seconds


def test_lifespan_waits_for_tracker_poller_before_releasing_lease(
    db_engine, test_settings, monkeypatch
):
    entered = threading.Event()
    close_context = threading.Event()
    poller_started = threading.Event()
    finish_poller = threading.Event()
    lease_released = threading.Event()

    class Lease:
        def __init__(self, root, name):
            del root, name

        def try_acquire(self):
            return True

        def release(self):
            lease_released.set()

    async def blocking_poller(session_factory, stop_event, **kwargs):
        del session_factory, kwargs
        poller_started.set()
        await asyncio.to_thread(finish_poller.wait)
        assert stop_event.is_set()

    _patch_idle_workers(monkeypatch)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(main, "live_merge_enabled", lambda: True)
    monkeypatch.setattr(main, "JobLease", Lease)
    monkeypatch.setattr(main, "run_tracker_notification_loop", blocking_poller, raising=False)

    def serve():
        with TestClient(main.create_app()):
            entered.set()
            close_context.wait(timeout=2)

    thread = threading.Thread(target=serve)
    thread.start()
    assert entered.wait(timeout=1)
    try:
        assert poller_started.wait(timeout=1)
        close_context.set()
        assert not lease_released.wait(timeout=0.1)
    finally:
        close_context.set()
        finish_poller.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert lease_released.is_set()
