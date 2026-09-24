"""Database-backed presence and bounded, payload-free system measurements."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    delete,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from robopark_api.models import AccessStatus, Base, Role, User, UserPark
from robopark_api.notification_delivery_models import NotificationDelivery
from robopark_api.services.operational_health import cached_host_snapshot
from robopark_api.services.ops.context import resolved_ops_dir
from robopark_api.services.sync_health import sync_health

PRESENCE_TTL = timedelta(minutes=2)
RAW_TTL = timedelta(hours=24)
AGGREGATE_TTL = timedelta(days=7)
BUCKET_SECONDS = 300
logger = logging.getLogger(__name__)


class UserPresence(Base):
    __tablename__ = "user_presence"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PresenceSample(Base):
    __tablename__ = "presence_samples"
    __table_args__ = (
        UniqueConstraint("user_id", "bucket_at", name="uq_presence_sample_user_bucket"),
        Index("ix_presence_samples_bucket", "bucket_at", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    bucket_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetricRaw(Base):
    __tablename__ = "system_metric_raw"
    __table_args__ = (Index("ix_system_metric_raw_sampled", "sampled_at", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON)


class MetricAggregate(Base):
    __tablename__ = "system_metric_aggregates"

    bucket_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _bucket(now: datetime) -> datetime:
    return datetime.fromtimestamp(int(now.timestamp() // BUCKET_SECONDS) * BUCKET_SECONDS, UTC)


def heartbeat(db: Session, user: User, *, now: datetime | None = None) -> None:
    current = now or datetime.now(UTC)
    row = db.get(UserPresence, user.id)
    if row is None:
        db.add(UserPresence(user_id=user.id, last_seen_at=current))
    else:
        row.last_seen_at = current
    bucket = _bucket(current)
    if (
        db.scalar(
            select(PresenceSample.id).where(
                PresenceSample.user_id == user.id, PresenceSample.bucket_at == bucket
            )
        )
        is None
    ):
        db.add(PresenceSample(user_id=user.id, bucket_at=bucket))
    db.commit()


def online_counts(db: Session, *, actor: User, now: datetime | None = None) -> dict:
    from robopark_api.services.rbac import is_royal

    current = now or datetime.now(UTC)
    allowed_parks = set(db.scalars(select(UserPark.park_id).where(UserPark.user_id == actor.id)))
    query = (
        select(User.id, Role.slug)
        .join(UserPresence, UserPresence.user_id == User.id)
        .join(Role, Role.id == User.role_id)
        .where(
            User.is_active.is_(True),
            User.access_status == AccessStatus.approved.value,
            UserPresence.last_seen_at > current - PRESENCE_TTL,
        )
    )
    if not is_royal(actor):
        query = (
            query.join(UserPark, UserPark.user_id == User.id)
            .where(UserPark.park_id.in_(allowed_parks))
            .distinct()
        )
    users = db.execute(query).all()
    ids = {user_id for user_id, _ in users}
    by_role: dict[str, int] = {}
    for _, role in users:
        by_role[role] = by_role.get(role, 0) + 1
    by_park: dict[str, int] = {}
    if ids:
        parks = db.execute(
            select(UserPark.user_id, UserPark.park_id).where(UserPark.user_id.in_(ids))
        )
        for _, park_id in parks:
            if is_royal(actor) or park_id in allowed_parks:
                key = str(park_id)
                by_park[key] = by_park.get(key, 0) + 1
    return {"total": len(ids), "by_role": by_role, "by_park": by_park}


def active_user_history(db: Session, *, actor: User, now: datetime | None = None) -> list[dict]:
    from robopark_api.services.rbac import is_royal

    current = now or datetime.now(UTC)
    query = select(PresenceSample.user_id, PresenceSample.bucket_at).where(
        PresenceSample.bucket_at >= current - AGGREGATE_TTL
    )
    if not is_royal(actor):
        allowed = select(UserPark.user_id).where(
            UserPark.park_id.in_(select(UserPark.park_id).where(UserPark.user_id == actor.id))
        )
        query = query.where(PresenceSample.user_id.in_(allowed))
    days: dict[str, set[int]] = {}
    for user_id, bucket_at in db.execute(query):
        days.setdefault(_aware(bucket_at).date().isoformat(), set()).add(user_id)
    return [{"date": day, "users": len(ids)} for day, ids in sorted(days.items())]


def push_health(db: Session) -> dict:
    pending = (
        db.scalar(
            select(func.count(NotificationDelivery.id)).where(
                NotificationDelivery.state.in_(("pending", "retry_wait", "sending"))
            )
        )
        or 0
    )
    failed = (
        db.scalar(
            select(func.count(NotificationDelivery.id)).where(
                NotificationDelivery.state == "needs_attention"
            )
        )
        or 0
    )
    return {"pending": pending, "needs_attention": failed}


def collect_system_metrics(db: Session, *, now: datetime | None = None, settings=None) -> dict:
    """Sample finite host/queue state once; keep only a five-minute summary long term."""
    current = now or datetime.now(UTC)
    health = sync_health(db, now=current).model_dump(mode="json")
    data = {"sync": health, "push": push_health(db)}
    if settings is not None:
        data["host"] = cached_host_snapshot(
            Path(settings.host_data_path),
            resolved_ops_dir(settings),
            Path(settings.host_health_path),
        )
    numeric = {
        "outbox_pending": float(health["pending_action_count"]),
        "outbox_attention": float(health["needs_attention_count"]),
        "push_pending": float(data["push"]["pending"]),
        "push_attention": float(data["push"]["needs_attention"]),
        "worker_active": float(health["worker_lease_state"] == "active"),
    }
    db.add(MetricRaw(sampled_at=current, data=data))
    bucket = _bucket(current)
    row = db.get(MetricAggregate, bucket)
    if row is None:
        db.add(
            MetricAggregate(bucket_at=bucket, sample_count=1, data={**data, "averages": numeric})
        )
    else:
        old = row.data.get("averages", {})
        averages = {
            key: (float(old.get(key, 0)) * row.sample_count + value) / (row.sample_count + 1)
            for key, value in numeric.items()
        }
        row.sample_count += 1
        row.data = {**data, "averages": averages}
    db.commit()
    return data


def metric_history(db: Session, *, now: datetime | None = None, days: int = 7) -> list[dict]:
    current = now or datetime.now(UTC)
    rows = db.scalars(
        select(MetricAggregate)
        .where(MetricAggregate.bucket_at >= current - timedelta(days=days))
        .order_by(MetricAggregate.bucket_at)
    )
    return [
        {"bucket_at": row.bucket_at.isoformat(), "sample_count": row.sample_count, **row.data}
        for row in rows
    ]


def prune_observability(
    db: Session, *, now: datetime | None = None, limit: int = 500
) -> tuple[int, int]:
    current = now or datetime.now(UTC)
    raw_ids = list(
        db.scalars(
            select(MetricRaw.id)
            .where(MetricRaw.sampled_at < current - RAW_TTL)
            .order_by(MetricRaw.sampled_at, MetricRaw.id)
            .limit(limit)
        )
    )
    if raw_ids:
        db.execute(delete(MetricRaw).where(MetricRaw.id.in_(raw_ids)))
    aggregate_keys = list(
        db.scalars(
            select(MetricAggregate.bucket_at)
            .where(MetricAggregate.bucket_at < current - AGGREGATE_TTL)
            .order_by(MetricAggregate.bucket_at)
            .limit(limit)
        )
    )
    if aggregate_keys:
        db.execute(delete(MetricAggregate).where(MetricAggregate.bucket_at.in_(aggregate_keys)))
    sample_ids = list(
        db.scalars(
            select(PresenceSample.id)
            .where(PresenceSample.bucket_at < current - AGGREGATE_TTL)
            .order_by(PresenceSample.bucket_at)
            .limit(limit)
        )
    )
    if sample_ids:
        db.execute(delete(PresenceSample).where(PresenceSample.id.in_(sample_ids)))
    stale_users = list(
        db.scalars(
            select(UserPresence.user_id)
            .where(UserPresence.last_seen_at < current - AGGREGATE_TTL)
            .order_by(UserPresence.last_seen_at, UserPresence.user_id)
            .limit(limit)
        )
    )
    if stale_users:
        db.execute(delete(UserPresence).where(UserPresence.user_id.in_(stale_users)))
    db.commit()
    return len(raw_ids), len(aggregate_keys)


async def run_metric_collection_loop(
    session_factory, stop: asyncio.Event, *, interval_seconds: float = 60, settings=None
) -> None:
    while not stop.is_set():

        def sample() -> None:
            with session_factory() as db:
                collect_system_metrics(db, settings=settings)

        try:
            await asyncio.to_thread(sample)
        except Exception:  # noqa: BLE001 - telemetry failure cannot stop delivery jobs.
            logger.exception("system metric sample failed")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
