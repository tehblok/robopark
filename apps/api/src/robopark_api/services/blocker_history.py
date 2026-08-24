from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.models import Park, ParkBlockerHistory
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.tracker_client import TrackerError, count_issues
from robopark_api.services.tracker_metrics import (
    build_arrived_in_window_query,
    build_departed_in_window_query,
)

BUCKET_SECONDS = 2 * 60 * 60
# v1 scans parks sequentially; raise only with Tracker rate-limit evidence.
MAX_PARK_SCAN_PARALLELISM = 1

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def align_bucket_start(now: datetime) -> datetime:
    """Align *now* to the start of the current 2h bucket on an even-hour grid."""
    now_utc = _as_utc(now)
    aligned = now_utc.replace(minute=0, second=0, microsecond=0)
    return aligned - timedelta(hours=now_utc.hour % 2)


def closed_bucket_window(now: datetime) -> tuple[datetime, datetime]:
    """Return ``[start, end)`` for the most recently closed 2h bucket at *now*."""
    bucket_end = align_bucket_start(now)
    bucket_start = bucket_end - timedelta(seconds=BUCKET_SECONDS)
    return bucket_start, bucket_end


def _park_scan_config(park: Park) -> tuple[str, str, str, str | None] | None:
    if not park.is_active:
        return None
    queue = (park.tracker_queue or "").strip()
    tag = (park.tag or "").strip()
    if not queue or not tag:
        return None
    priority = (park.tracker_priority or "blocker").strip() or "blocker"
    issue_type = (park.tracker_type or "").strip() or None
    return queue, tag, priority, issue_type


def scan_park_bucket(
    db: Session,
    park: Park,
    bucket_start: datetime,
    bucket_end: datetime,
    *,
    token: str,
) -> tuple[int, int]:
    config = _park_scan_config(park)
    if config is None:
        return 0, 0
    queue, tag, priority, issue_type = config
    arrived_query = build_arrived_in_window_query(
        queue,
        tag,
        bucket_start,
        bucket_end,
        priority=priority,
        issue_type=issue_type,
    )
    departed_query = build_departed_in_window_query(
        queue,
        tag,
        bucket_start,
        bucket_end,
        priority=priority,
        issue_type=issue_type,
    )
    arrived_count = count_issues(token=token, query=arrived_query)
    departed_count = count_issues(token=token, query=departed_query)
    upsert_bucket(
        db,
        park_id=park.id,
        bucket_start=bucket_start,
        arrived_count=arrived_count,
        departed_count=departed_count,
    )
    return arrived_count, departed_count


def scan_all_parks_once(db: Session, *, now: datetime | None = None) -> int:
    token = settings_svc.get_tracker_token(db)
    if not token:
        logger.warning("Blocker history scan skipped: tracker token not configured")
        return 0

    now_utc = _as_utc(now or datetime.now(timezone.utc))
    bucket_start, bucket_end = closed_bucket_window(now_utc)

    parks = db.scalars(select(Park)).all()
    scanned = 0
    for park in parks:
        if _park_scan_config(park) is None:
            continue
        try:
            scan_park_bucket(
                db,
                park,
                bucket_start,
                bucket_end,
                token=token,
            )
        except TrackerError:
            logger.warning(
                "Blocker history scan failed for park %s (%s)",
                park.id,
                park.tag,
                exc_info=True,
            )
            continue
        scanned += 1
    if scanned > 0:
        delete_old_buckets(db, retention_days=30)
    return scanned


def upsert_bucket(
    db: Session,
    *,
    park_id: int,
    bucket_start: datetime,
    arrived_count: int,
    departed_count: int,
    scanned_at: datetime | None = None,
) -> ParkBlockerHistory:
    bucket_start = _as_utc(bucket_start)
    scanned = _as_utc(scanned_at or datetime.now(timezone.utc))

    row = db.scalar(
        select(ParkBlockerHistory).where(
            ParkBlockerHistory.park_id == park_id,
            ParkBlockerHistory.bucket_start == bucket_start,
        )
    )
    if row is None:
        row = ParkBlockerHistory(
            park_id=park_id,
            bucket_start=bucket_start,
            arrived_count=arrived_count,
            departed_count=departed_count,
            scanned_at=scanned,
        )
        db.add(row)
    else:
        row.arrived_count = arrived_count
        row.departed_count = departed_count
        row.scanned_at = scanned
    db.commit()
    db.refresh(row)
    return row


def history_series(
    db: Session,
    *,
    park_id: int,
    days: int = 7,
) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.scalars(
        select(ParkBlockerHistory)
        .where(
            ParkBlockerHistory.park_id == park_id,
            ParkBlockerHistory.bucket_start >= cutoff,
        )
        .order_by(ParkBlockerHistory.bucket_start)
    ).all()
    return [
        {
            "bucket_start": row.bucket_start,
            "arrived_count": row.arrived_count,
            "departed_count": row.departed_count,
        }
        for row in rows
    ]


def delete_old_buckets(
    db: Session,
    *,
    park_id: int | None = None,
    retention_days: int = 30,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    stmt = delete(ParkBlockerHistory).where(ParkBlockerHistory.bucket_start < cutoff)
    if park_id is not None:
        stmt = stmt.where(ParkBlockerHistory.park_id == park_id)
    result = db.execute(stmt)
    db.commit()
    return result.rowcount
