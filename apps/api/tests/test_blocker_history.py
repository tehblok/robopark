from datetime import datetime, timedelta, timezone

import pytest

from robopark_api.models import Park
from robopark_api.services.blocker_history import (
    delete_old_buckets,
    history_series,
    upsert_bucket,
)


@pytest.fixture
def seed_park(db_session):
    park = Park(name="Alpha", tag="Alpha", is_active=True)
    db_session.add(park)
    db_session.commit()
    db_session.refresh(park)
    return park


def test_upsert_bucket_idempotent(db_session, seed_park):
    t0 = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
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
    rows = history_series(db_session, park_id=seed_park.id, days=7)
    assert len(rows) == 1
    assert rows[0]["arrived_count"] == 5
    assert rows[0]["departed_count"] == 3


def test_history_series_filters_by_days(db_session, seed_park):
    now = datetime.now(timezone.utc)
    recent = now.replace(minute=0, second=0, microsecond=0)
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
    rows = history_series(db_session, park_id=seed_park.id, days=7)
    assert len(rows) == 1
    assert rows[0]["arrived_count"] == 1


def test_delete_old_buckets(db_session, seed_park):
    now = datetime.now(timezone.utc)
    recent = now.replace(minute=0, second=0, microsecond=0)
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
    deleted = delete_old_buckets(db_session, park_id=seed_park.id, retention_days=30)
    assert deleted == 1
    rows = history_series(db_session, park_id=seed_park.id, days=365)
    assert len(rows) == 1
    assert rows[0]["arrived_count"] == 1
