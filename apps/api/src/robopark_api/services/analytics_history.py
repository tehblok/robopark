"""Immutable observed state, collected independently from cumulative flow counters."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.models import AnalyticsObservation, AnalyticsSnapshot, Park
from robopark_api.services import platform_settings, tracker_client
from robopark_api.services.blocker_history import align_bucket_start
from robopark_api.services.operations import age_hours, as_utc, get_sla_target
from robopark_api.services.tracker_filters import issue_status_bucket, park_priority_type
from robopark_api.services.tracker_policy import issue_tags

logger = logging.getLogger(__name__)


def record_observation(
    db: Session,
    *,
    park: Park,
    issues: list[dict],
    observed_at: datetime,
    target_hours: int | None,
) -> AnalyticsSnapshot:
    observed_at = as_utc(observed_at)
    bucket = align_bucket_start(observed_at)
    existing = db.get(AnalyticsSnapshot, (park.id, bucket))
    if existing is not None:
        return existing
    snapshot = AnalyticsSnapshot(
        park_id=park.id, bucket_start=bucket, observed_at=observed_at, target_hours=target_hours
    )
    db.add(snapshot)
    db.flush()
    seen = set()
    for item in issues:
        key = str(item.get("key") or "").strip()
        if (
            not key
            or key in seen
            or item.get("queue") != park.tracker_queue
            or park.tag not in issue_tags(item)
        ):
            continue
        seen.add(key)
        status = str(item.get("status_key") or item.get("status") or "unknown")
        db.add(
            AnalyticsObservation(
                park_id=park.id,
                bucket_start=bucket,
                issue_key=key,
                status=status,
                status_bucket=issue_status_bucket(item),
                age_hours=age_hours(item, observed_at),
            )
        )
    db.commit()
    return snapshot


def scan_all_parks_once(db: Session, *, now: datetime | None = None) -> int:
    token = platform_settings.get_tracker_token(db)
    if not token:
        return 0
    scanned = 0
    for park in db.scalars(select(Park).where(Park.is_active.is_(True))).all():
        if not park.feature_blockers or not park.tracker_queue or not park.tag:
            continue
        if db.get(AnalyticsSnapshot, (park.id, align_bucket_start(now or datetime.now(UTC)))):
            continue
        priority, issue_type = park_priority_type(park)
        try:
            issues = tracker_client.fetch_park_blockers(
                token=token,
                queue=park.tracker_queue,
                park_tag=park.tag,
                priority=priority,
                issue_type=issue_type,
            )
            record_observation(
                db,
                park=park,
                issues=issues,
                observed_at=now or datetime.now(UTC),
                target_hours=get_sla_target(db, park.id),
            )
        except tracker_client.TrackerError:
            logger.warning("Analytics observation failed for park %s", park.id, exc_info=True)
            continue
        scanned += 1
    # Explicit deletion of child rows also supports SQLite without FK enforcement.
    cutoff = align_bucket_start(now or datetime.now(UTC)) - timedelta(days=30)
    db.execute(delete(AnalyticsObservation).where(AnalyticsObservation.bucket_start < cutoff))
    db.execute(delete(AnalyticsSnapshot).where(AnalyticsSnapshot.bucket_start < cutoff))
    db.commit()
    return scanned
