from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.models import ParkBlockerHistory


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
