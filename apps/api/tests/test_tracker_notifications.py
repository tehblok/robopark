import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.config import Settings
from robopark_api.models import Park
from robopark_api.schedule_models import TrackerNotificationCursor
from robopark_api.services import (
    platform_settings,
    tracker_api,
    tracker_cache,
    tracker_client,
    tracker_notifications,
    worker_runtime,
)
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


def _seed_cursor(db_session, created: str = "2026-09-20T18:00:00+00:00", key: str = ""):
    db_session.add(
        TrackerNotificationCursor(
            scope_key="new-tasks",
            cursor_value=json.dumps([created, key], separators=(",", ":")),
        )
    )
    db_session.commit()


def test_poller_bootstraps_without_emitting_historical_tasks(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    searches = []
    monkeypatch.setattr(
        tracker_cache,
        "search_issue_page",
        lambda **kwargs: searches.append(kwargs)
        or [_issue("ROBOPARK-OLD", "2026-08-20T18:00:00Z")],
    )
    emitted = []

    processed = poll_tracker_notifications(
        _factory(db_engine), _capture(emitted), page_size=10, owner_id="worker-a"
    )

    assert processed == 0
    assert emitted == []
    assert searches == []
    with Session(db_engine) as db:
        cursor = db.get(TrackerNotificationCursor, "new-tasks")
        assert cursor is not None
        created, key = json.loads(cursor.cursor_value)
        assert datetime.now(UTC) - datetime.fromisoformat(created) < timedelta(seconds=5)
        assert key == ""


def test_poller_rebootstraps_invalid_cursor_without_historical_fetch(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    db_session.add(
        TrackerNotificationCursor(scope_key="new-tasks", cursor_value="not-a-cursor")
    )
    db_session.commit()
    searches = []
    monkeypatch.setattr(
        tracker_cache,
        "search_issue_page",
        lambda **kwargs: searches.append(kwargs)
        or [_issue("ROBOPARK-OLD", "2026-08-20T18:00:00Z")],
    )

    processed = poll_tracker_notifications(
        _factory(db_engine), lambda **_event: None, page_size=10, owner_id="worker-a"
    )

    assert processed == 0
    assert searches == []
    with Session(db_engine) as db:
        cursor = db.get(TrackerNotificationCursor, "new-tasks")
        assert cursor is not None
        assert json.loads(cursor.cursor_value)[1] == ""


def test_tracker_page_fetch_stops_sdk_iterator_at_limit(monkeypatch):
    search_page = getattr(tracker_client, "search_issue_page", None)
    assert callable(search_page)
    yielded = []
    captured = {}

    class Issues:
        def find(self, query, **kwargs):
            captured.update(query=query, **kwargs)
            for index in range(1000):
                yielded.append(index)
                yield {
                    "key": f"ROBOPARK-{index}",
                    "createdAt": f"2026-09-20T18:{index:02d}:00Z",
                    "status": {"key": "queued", "display": "В очереди"},
                }

    client = type("Client", (), {"issues": Issues()})()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: client)
    monkeypatch.setattr(
        tracker_client,
        "_run_tracked",
        lambda operation, **_kwargs: operation(),
    )

    items = search_page(
        token="token",
        query="Queue: ROBOPARK",
        filter_open=False,
        order=["createdAt"],
        limit=2,
    )

    assert [item["key"] for item in items] == ["ROBOPARK-0", "ROBOPARK-1"]
    assert yielded == [0, 1]
    assert captured == {
        "query": "Queue: ROBOPARK",
        "per_page": tracker_client.API_PAGE_SIZE,
        "order": ["createdAt"],
    }


def test_tracker_page_limit_counts_rows_filtered_locally(monkeypatch):
    yielded = []

    class Issues:
        def find(self, _query, **_kwargs):
            rows = [
                {
                    "key": "ROBOPARK-CLOSED",
                    "createdAt": "2026-09-20T18:00:00Z",
                    "resolvedAt": "2026-09-20T18:01:00Z",
                    "status": {"key": "closed", "display": "Closed"},
                },
                {
                    "key": "ROBOPARK-OPEN",
                    "createdAt": "2026-09-20T18:02:00Z",
                    "status": {"key": "queued", "display": "В очереди"},
                },
                {
                    "key": "ROBOPARK-NEXT-PAGE",
                    "createdAt": "2026-09-20T18:03:00Z",
                    "status": {"key": "queued", "display": "В очереди"},
                },
            ]
            for row in rows:
                yielded.append(row["key"])
                yield row

    client = type("Client", (), {"issues": Issues()})()
    monkeypatch.setattr(tracker_client, "_client", lambda _token: client)
    monkeypatch.setattr(
        tracker_client,
        "_run_tracked",
        lambda operation, **_kwargs: operation(),
    )

    items = tracker_client.search_issue_page(
        token="token",
        query="Queue: ROBOPARK",
        filter_open=True,
        limit=2,
    )

    assert [item["key"] for item in items] == ["ROBOPARK-OPEN"]
    assert yielded == ["ROBOPARK-CLOSED", "ROBOPARK-OPEN"]


def test_poller_query_keeps_queue_and_cursor_boundary():
    query = tracker_notifications._search_query(
        ["ROBOPARK"],
        (datetime(2026, 9, 20, 18, 30, tzinfo=UTC), "ROBOPARK-50"),
    )

    assert "Queue: ROBOPARK" in query
    assert 'Created: > "2026-09-20T18:30:00Z"' in query
    assert 'Key: > "ROBOPARK-50"' in query


def test_poller_keyset_progresses_past_full_boundary_page(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _seed_cursor(db_session, created="2026-09-20T17:59:59+00:00")
    same_second = "2026-09-20T18:00:00Z"
    first_page = [
        _issue("ROBOPARK-1", same_second),
        _issue("ROBOPARK-2", same_second),
    ]
    last_page = [_issue("ROBOPARK-3", same_second)]
    calls = []

    def search_page(**kwargs):
        calls.append(kwargs)
        if 'Key: > "ROBOPARK-2"' in kwargs["query"]:
            return last_page
        return first_page

    monkeypatch.setattr(tracker_cache, "search_issue_page", search_page)
    emitted = []
    factory = _factory(db_engine)

    assert (
        poll_tracker_notifications(factory, _capture(emitted), page_size=2, owner_id="worker-a")
        == 2
    )
    assert (
        poll_tracker_notifications(factory, _capture(emitted), page_size=2, owner_id="worker-b")
        == 1
    )

    assert [event["event_key"] for event in emitted] == [
        "new-task:ROBOPARK-1",
        "new-task:ROBOPARK-2",
        "new-task:ROBOPARK-3",
    ]
    assert calls[1]["order"] == ["createdAt", "key"]


def test_poller_advances_past_raw_page_filtered_out_locally(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _seed_cursor(db_session, created="2026-09-20T17:59:59+00:00")
    closed = {
        **_issue("ROBOPARK-1", "2026-09-20T18:00:00Z"),
        "status": "Closed",
        "status_key": "closed",
    }
    new = _issue("ROBOPARK-2", "2026-09-20T18:01:00Z")
    calls = []

    def search_page(**kwargs):
        calls.append(kwargs)
        return [new] if 'Key: > "ROBOPARK-1"' in kwargs["query"] else [closed]

    monkeypatch.setattr(tracker_cache, "search_issue_page", search_page)
    emitted = []
    factory = _factory(db_engine)

    assert poll_tracker_notifications(
        factory, _capture(emitted), page_size=1, owner_id="worker-a"
    ) == 0
    assert poll_tracker_notifications(
        factory, _capture(emitted), page_size=1, owner_id="worker-b"
    ) == 1

    assert calls[0]["filter_open"] is False
    assert [event["event_key"] for event in emitted] == ["new-task:ROBOPARK-2"]


def test_poller_does_not_guess_park_for_shared_queue(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _seed_cursor(db_session)
    db_session.add(
        Park(name="Beta", tag="Beta", tracker_queue="ROBOPARK", is_active=True)
    )
    db_session.commit()
    issue = {**_issue("ROBOPARK-20", "2026-09-20T18:05:00Z"), "tags": []}
    monkeypatch.setattr(tracker_cache, "search_issue_page", lambda **_kwargs: [issue])
    emitted = []

    processed = poll_tracker_notifications(
        _factory(db_engine), _capture(emitted), page_size=10, owner_id="worker-a"
    )

    assert processed == 0
    assert emitted == []


def test_poller_does_not_emit_for_inactive_tagged_park(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _seed_cursor(db_session)
    db_session.add(
        Park(name="Inactive Beta", tag="Beta", tracker_queue="ROBOPARK", is_active=False)
    )
    db_session.commit()
    issue = {**_issue("ROBOPARK-21", "2026-09-20T18:06:00Z"), "tags": ["Beta"]}
    monkeypatch.setattr(tracker_cache, "search_issue_page", lambda **_kwargs: [issue])
    emitted = []

    processed = poll_tracker_notifications(
        _factory(db_engine), _capture(emitted), page_size=10, owner_id="worker-a"
    )

    assert processed == 0
    assert emitted == []


def test_tracker_notification_settings_reject_lease_shorter_than_poll_bound():
    with pytest.raises(ValidationError, match="tracker_notification_lease_too_short"):
        Settings(
            _env_file=None,
            tracker_notification_poll_deadline_seconds=31,
            tracker_notification_lease_seconds=40,
            push_delivery_deadline_seconds=30,
        )


def test_tracker_retry_bound_matches_search_lease_contract():
    upper_bound = getattr(tracker_api, "call_with_retry_upper_bound", None)
    assert callable(upper_bound)

    tracker_bound = upper_bound(
        max_attempts=2,
        slot_timeout=25,
        call_timeout=30,
        base_delay=0.5,
    )

    assert tracker_bound == pytest.approx(224.8)
    assert pytest.approx(tracker_bound) == tracker_client.SEARCH_OPERATION_TIMEOUT_SECONDS
    required_lease = 31 + tracker_bound + 5
    accepted = Settings(
        _env_file=None,
        tracker_notification_poll_deadline_seconds=31,
        tracker_notification_lease_seconds=required_lease,
        push_delivery_deadline_seconds=10,
    )
    assert accepted.tracker_notification_lease_seconds == pytest.approx(required_lease)
    with pytest.raises(ValidationError, match="tracker_notification_lease_too_short"):
        Settings(
            _env_file=None,
            tracker_notification_poll_deadline_seconds=31,
            tracker_notification_lease_seconds=required_lease - 0.01,
            push_delivery_deadline_seconds=10,
        )


def test_poller_stops_page_immediately_after_losing_lease(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _seed_cursor(db_session)
    monkeypatch.setattr(
        tracker_cache,
        "search_issue_page",
        lambda **_kwargs: [
            _issue("ROBOPARK-30", "2026-09-20T18:10:00Z"),
            _issue("ROBOPARK-31", "2026-09-20T18:11:00Z"),
        ],
    )
    emitted = []

    def steal_lease(**event):
        emitted.append(event)
        with Session(db_engine) as db:
            cursor = db.get(TrackerNotificationCursor, "new-tasks")
            cursor.lease_owner = "worker-b"
            cursor.lease_until = datetime.now(UTC) + timedelta(minutes=5)
            db.commit()

    processed = poll_tracker_notifications(
        _factory(db_engine), steal_lease, page_size=10, owner_id="worker-a"
    )

    assert processed == 1
    assert [event["event_key"] for event in emitted] == ["new-task:ROBOPARK-30"]


def test_poller_deadline_prevents_starting_another_emit(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _seed_cursor(db_session)
    monkeypatch.setattr(
        tracker_cache,
        "search_issue_page",
        lambda **_kwargs: [
            _issue("ROBOPARK-40", "2026-09-20T18:20:00Z"),
            _issue("ROBOPARK-41", "2026-09-20T18:21:00Z"),
        ],
    )
    emitted = []
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(tracker_notifications, "monotonic", lambda: next(clock))

    def emit(**event):
        emitted.append(event)

    processed = poll_tracker_notifications(
        _factory(db_engine),
        emit,
        page_size=10,
        owner_id="worker-a",
        lease_seconds=2,
        poll_deadline_seconds=1,
        max_operation_seconds=0.05,
    )

    assert processed == 1
    assert [event["event_key"] for event in emitted] == ["new-task:ROBOPARK-40"]


def test_notification_loop_owns_and_joins_its_poll_thread(monkeypatch):
    started = threading.Event()
    finish = threading.Event()
    worker_names = []

    def bounded_poll(*_args, **_kwargs):
        worker_names.append(threading.current_thread().name)
        started.set()
        finish.wait(timeout=0.2)
        return 0

    monkeypatch.setattr(tracker_notifications, "poll_tracker_notifications", bounded_poll)

    async def exercise():
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            tracker_notifications.run_tracker_notification_loop(
                _factory(None),
                stop_event,
                emit=lambda **_kwargs: None,
                interval_seconds=60,
                page_size=1,
                lease_seconds=60,
                poll_deadline_seconds=31,
                max_operation_seconds=30,
            )
        )
        assert await asyncio.to_thread(started.wait, 0.5)
        stop_event.set()
        finish.set()
        await asyncio.wait_for(task, timeout=0.5)

    asyncio.run(exercise())

    assert worker_names and worker_names[0].startswith("tracker-notification-poll")
    assert not any(
        thread.name.startswith("tracker-notification-poll") and thread.is_alive()
        for thread in threading.enumerate()
    )


def test_two_workers_emit_one_event_for_the_same_issue(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    del seed_park_with_tracker
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _seed_cursor(db_session)
    monkeypatch.setattr(
        tracker_cache,
        "search_issue_page",
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
    _seed_cursor(db_session)
    issues = [_issue("ROBOPARK-11", "2026-09-20T18:02:00Z")]
    monkeypatch.setattr(tracker_cache, "search_issue_page", lambda **_kwargs: list(issues))
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
    _seed_cursor(db_session)
    attempts = 0

    def search(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise tracker_client.TrackerError("token leaked? no")
        return [_issue("ROBOPARK-13", "2026-09-20T18:04:00Z")]

    monkeypatch.setattr(tracker_cache, "search_issue_page", search)
    emitted = []
    factory = _factory(db_engine)

    assert (
        poll_tracker_notifications(factory, _capture(emitted), page_size=10, owner_id="worker-a")
        == 0
    )
    with Session(db_engine) as db:
        failed = db.get(TrackerNotificationCursor, "new-tasks")
        assert failed is not None
        assert json.loads(failed.cursor_value) == ["2026-09-20T18:00:00+00:00", ""]
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
        monkeypatch.setattr(worker_runtime, name, idle)
    monkeypatch.setattr(worker_runtime, "run_tracker_outbox_loop", idle_with_factory)
    monkeypatch.setattr(worker_runtime, "run_campaign_refresh_loop", idle_with_factory)


@pytest.mark.parametrize("won_lease", [False, True])
def test_worker_starts_tracker_poller_only_for_lease_owner(
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
        assert session_factory is factory
        started.set()
        await stop_event.wait()

    factory = sessionmaker(bind=db_engine, future=True)
    _patch_idle_workers(monkeypatch)
    monkeypatch.setattr(worker_runtime, "host_maintenance_active", lambda _settings: False)
    monkeypatch.setattr(worker_runtime, "JobLease", Lease)
    monkeypatch.setattr(worker_runtime, "run_tracker_notification_loop", notification_loop)

    class Push:
        def emit(self, **kwargs):
            return {}

        def close(self):
            pass

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(
            worker_runtime.WorkerRuntime(test_settings, factory, Push()).start(stop)
        )
        if won_lease:
            assert await asyncio.to_thread(started.wait, 1)
        else:
            await asyncio.sleep(0.05)
            assert not started.is_set()
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())

    assert started.is_set() is won_lease
    if won_lease:
        assert captured["interval_seconds"] == test_settings.tracker_notification_interval_seconds
        assert captured["page_size"] == test_settings.tracker_notification_page_size
        assert captured["lease_seconds"] == test_settings.tracker_notification_lease_seconds


def test_worker_waits_for_tracker_poller_before_releasing_lease(
    db_engine, test_settings, monkeypatch
):
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

    factory = sessionmaker(bind=db_engine, future=True)
    _patch_idle_workers(monkeypatch)
    monkeypatch.setattr(worker_runtime, "host_maintenance_active", lambda _settings: False)
    monkeypatch.setattr(worker_runtime, "JobLease", Lease)
    monkeypatch.setattr(worker_runtime, "run_tracker_notification_loop", blocking_poller)

    class Push:
        def emit(self, **kwargs):
            return {}

        def close(self):
            pass

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(
            worker_runtime.WorkerRuntime(test_settings, factory, Push()).start(stop)
        )
        assert await asyncio.to_thread(poller_started.wait, 1)
        stop.set()
        try:
            await asyncio.sleep(0.05)
            assert not lease_released.is_set()
        finally:
            finish_poller.set()
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert lease_released.is_set()
