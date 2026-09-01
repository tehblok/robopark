import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from robopark_api.models import Park
from robopark_api.services import blocker_history as history_svc
from robopark_api.services import blocker_history_job, tracker_metrics
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.blocker_history import (
    align_bucket_start,
    closed_bucket_window,
    delete_old_buckets,
    history_series,
    scan_all_parks_once,
    scan_park_bucket,
    upsert_bucket,
)
from robopark_api.services.tracker_client import TrackerError

FIXED_NOW = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


@pytest.fixture
def seed_park(db_session):
    park = Park(name="Alpha", tag="Alpha", is_active=True)
    db_session.add(park)
    db_session.commit()
    db_session.refresh(park)
    return park


@pytest.fixture
def seed_park_with_tracker(db_session):
    park = Park(
        name="Alpha",
        tag="Alpha",
        is_active=True,
        tracker_queue="ROBOPARK",
        tracker_priority="blocker",
        tracker_type="bug",
    )
    db_session.add(park)
    db_session.commit()
    db_session.refresh(park)
    return park


def test_upsert_bucket_idempotent(db_session, seed_park):
    t0 = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    upsert_bucket(
        db_session,
        park_id=seed_park.id,
        bucket_start=t0,
        arrived_count=2,
        departed_count=1,
    )
    upsert_bucket(
        db_session,
        park_id=seed_park.id,
        bucket_start=t0,
        arrived_count=5,
        departed_count=3,
    )
    rows = history_series(db_session, park_id=seed_park.id, days=7, now=FIXED_NOW)
    assert len(rows) == 1
    assert rows[0]["arrived_count"] == 5
    assert rows[0]["departed_count"] == 3


def test_history_series_filters_by_days(db_session, seed_park):
    recent = FIXED_NOW.replace(minute=0, second=0, microsecond=0)
    old = recent - timedelta(days=10)
    upsert_bucket(
        db_session,
        park_id=seed_park.id,
        bucket_start=recent,
        arrived_count=1,
        departed_count=0,
    )
    upsert_bucket(
        db_session,
        park_id=seed_park.id,
        bucket_start=old,
        arrived_count=9,
        departed_count=9,
    )
    rows = history_series(db_session, park_id=seed_park.id, days=7, now=FIXED_NOW)
    assert len(rows) == 1
    assert rows[0]["arrived_count"] == 1


def test_history_series_retains_record_exactly_at_cutoff(db_session, seed_park):
    cutoff = FIXED_NOW - timedelta(days=7)
    upsert_bucket(
        db_session,
        park_id=seed_park.id,
        bucket_start=cutoff,
        arrived_count=4,
        departed_count=0,
    )

    rows = history_series(db_session, park_id=seed_park.id, days=7, now=FIXED_NOW)

    assert len(rows) == 1
    assert rows[0]["bucket_start"].replace(tzinfo=UTC) == cutoff


def test_delete_old_buckets(db_session, seed_park):
    recent = FIXED_NOW.replace(minute=0, second=0, microsecond=0)
    old = recent - timedelta(days=40)
    upsert_bucket(
        db_session,
        park_id=seed_park.id,
        bucket_start=recent,
        arrived_count=1,
        departed_count=0,
    )
    upsert_bucket(
        db_session,
        park_id=seed_park.id,
        bucket_start=old,
        arrived_count=9,
        departed_count=9,
    )
    deleted = delete_old_buckets(db_session, park_id=seed_park.id, retention_days=30, now=FIXED_NOW)
    assert deleted == 1
    rows = history_series(db_session, park_id=seed_park.id, days=365, now=FIXED_NOW)
    assert len(rows) == 1
    assert rows[0]["arrived_count"] == 1


def test_delete_old_buckets_retains_record_exactly_at_cutoff(db_session, seed_park):
    cutoff = FIXED_NOW - timedelta(days=30)
    upsert_bucket(
        db_session,
        park_id=seed_park.id,
        bucket_start=cutoff,
        arrived_count=4,
        departed_count=0,
    )

    deleted = delete_old_buckets(db_session, park_id=seed_park.id, retention_days=30, now=FIXED_NOW)

    assert deleted == 0
    rows = history_series(db_session, park_id=seed_park.id, days=365, now=FIXED_NOW)
    assert len(rows) == 1
    assert rows[0]["bucket_start"].replace(tzinfo=UTC) == cutoff


def test_align_bucket_start_even_hour_grid():
    assert align_bucket_start(datetime(2026, 8, 24, 14, 30, tzinfo=UTC)) == datetime(
        2026, 8, 24, 14, 0, tzinfo=UTC
    )
    assert align_bucket_start(datetime(2026, 8, 24, 13, 30, tzinfo=UTC)) == datetime(
        2026, 8, 24, 12, 0, tzinfo=UTC
    )
    assert align_bucket_start(datetime(2026, 8, 24, 11, 5, tzinfo=UTC)) == datetime(
        2026, 8, 24, 10, 0, tzinfo=UTC
    )


def test_closed_bucket_window_scans_previous_closed_bucket():
    now = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
    start, end = closed_bucket_window(now)
    assert start == datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 24, 14, 0, tzinfo=UTC)


def test_closed_bucket_window_at_exact_boundary():
    now = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    start, end = closed_bucket_window(now)
    assert start == datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 24, 14, 0, tzinfo=UTC)


def test_build_arrived_in_window_query_uses_park_filters():
    start = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    end = start + timedelta(hours=2)
    query = tracker_metrics.build_arrived_in_window_query(
        "ROBOPARK",
        "Alpha",
        start,
        end,
        priority="critical",
        issue_type="bug",
    )
    assert "Queue: ROBOPARK" in query
    assert "Priority: critical" in query
    assert "Tags: Alpha" in query
    assert "Type: bug" in query
    assert 'Created: >= "2026-08-24 10:00:00"' in query
    assert 'Created: < "2026-08-24 12:00:00"' in query


def test_build_departed_in_window_query_omits_empty_type():
    start = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    end = start + timedelta(hours=2)
    query = tracker_metrics.build_departed_in_window_query(
        "ROBOPARK",
        "Alpha",
        start,
        end,
    )
    assert "Type:" not in query
    assert 'Updated: >= "2026-08-24 10:00:00"' in query
    assert 'Updated: < "2026-08-24 12:00:00"' in query


def test_scan_park_bucket_upserts_counts(db_session, seed_park_with_tracker, monkeypatch):
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "token")
    bucket_start = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
    bucket_end = bucket_start + timedelta(hours=2)
    calls: list[str] = []

    def fake_count_issues(*, token: str, query: str) -> int:
        calls.append(query)
        assert token == "token"
        return 3 if "Created:" in query else 1

    monkeypatch.setattr(history_svc, "count_issues", fake_count_issues)

    arrived, departed = scan_park_bucket(
        db_session,
        seed_park_with_tracker,
        bucket_start,
        bucket_end,
        token="token",
    )

    assert arrived == 3
    assert departed == 1
    assert len(calls) == 2
    rows = history_series(db_session, park_id=seed_park_with_tracker.id, days=7, now=FIXED_NOW)
    assert len(rows) == 1
    assert rows[0]["arrived_count"] == 3
    assert rows[0]["departed_count"] == 1


def test_scan_all_parks_once_skips_inactive_and_unconfigured(
    db_session, seed_park_with_tracker, monkeypatch
):
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "token")
    inactive = Park(
        name="Inactive",
        tag="Inactive",
        is_active=False,
        tracker_queue="ROBOPARK",
    )
    missing_queue = Park(name="NoQueue", tag="NoQueue", is_active=True)
    db_session.add_all([inactive, missing_queue])
    db_session.commit()

    bucket_calls: list[tuple[datetime, datetime]] = []
    park_calls: list[int] = []

    def fake_scan(db, park, bucket_start, bucket_end, *, token):
        bucket_calls.append((bucket_start, bucket_end))
        park_calls.append(park.id)
        return 1, 0

    monkeypatch.setattr(history_svc, "scan_park_bucket", fake_scan)

    now = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
    scanned = scan_all_parks_once(db_session, now=now)

    assert scanned == 1
    assert bucket_calls == [
        (
            datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
            datetime(2026, 8, 24, 14, 0, tzinfo=UTC),
        )
    ]
    assert park_calls == [seed_park_with_tracker.id]


def test_scan_all_parks_once_continues_on_tracker_error(
    db_session, seed_park_with_tracker, monkeypatch
):
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "token")
    other = Park(
        name="Beta",
        tag="Beta",
        is_active=True,
        tracker_queue="ROBOPARK",
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    def fake_scan(db, park, bucket_start, bucket_end, *, token):
        if park.tag == "Alpha":
            raise TrackerError("upstream down")
        upsert_bucket(
            db,
            park_id=park.id,
            bucket_start=bucket_start,
            arrived_count=2,
            departed_count=0,
        )
        return 2, 0

    monkeypatch.setattr(history_svc, "scan_park_bucket", fake_scan)

    scanned = scan_all_parks_once(
        db_session,
        now=datetime(2026, 8, 24, 14, 30, tzinfo=UTC),
    )

    assert scanned == 1
    rows = history_series(db_session, park_id=other.id, days=7, now=FIXED_NOW)
    assert rows[0]["arrived_count"] == 2


def test_scan_all_parks_once_without_token_returns_zero(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(history_svc, "scan_park_bucket", lambda *a, **k: calls.append(1))
    assert scan_all_parks_once(db_session) == 0
    assert calls == []


def test_scan_all_parks_once_runs_retention_after_success(
    db_session, seed_park_with_tracker, monkeypatch
):
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "token")
    retention_calls: list[tuple[int, datetime]] = []

    monkeypatch.setattr(
        history_svc,
        "scan_park_bucket",
        lambda *a, **k: (1, 0),
    )
    monkeypatch.setattr(
        history_svc,
        "delete_old_buckets",
        lambda db, **kwargs: retention_calls.append((kwargs["retention_days"], kwargs["now"])),
    )

    scanned = scan_all_parks_once(
        db_session,
        now=FIXED_NOW.replace(tzinfo=None),
    )

    assert scanned == 1
    assert retention_calls == [(30, FIXED_NOW)]


def test_scan_all_parks_once_skips_retention_when_nothing_scanned(
    db_session, seed_park, monkeypatch
):
    settings_svc.set_setting(db_session, settings_svc.TRACKER_TOKEN_KEY, "token")
    retention_calls: list[int] = []

    monkeypatch.setattr(
        history_svc,
        "delete_old_buckets",
        lambda db, **kwargs: retention_calls.append(1) or 0,
    )

    assert scan_all_parks_once(db_session) == 0
    assert retention_calls == []


def test_run_blocker_history_loop_runs_once_with_zero_interval(monkeypatch):
    asyncio_stop = asyncio.Event()
    calls = []

    def fake_scan_all_parks_once(db, *, now=None):
        calls.append(now)
        asyncio_stop.set()
        return 0

    monkeypatch.setattr(blocker_history_job, "scan_all_parks_once", fake_scan_all_parks_once)

    asyncio.run(
        blocker_history_job.run_blocker_history_loop(
            asyncio_stop,
            interval_seconds=0,
        )
    )

    assert len(calls) == 1
