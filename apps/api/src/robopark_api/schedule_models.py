from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from robopark_api.models import Base


class ScheduleDateTime(TypeDecorator[datetime]):
    """Keep SQLite schedule boundaries in their historical Moscow wall time."""

    impl = DateTime(timezone=True)
    cache_ok = True
    _moscow = ZoneInfo("Europe/Moscow")

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None or dialect.name != "sqlite" or value.tzinfo is None:
            return value
        # This also normalizes query bounds. Reinterpreting old rows as UTC
        # would move existing shifts, so preserve their established convention.
        return value.astimezone(self._moscow).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None or dialect.name != "sqlite":
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=self._moscow)
        return value.astimezone(self._moscow)


class ScheduleEntry(Base):
    __tablename__ = "schedule_entries"
    __table_args__ = (
        CheckConstraint("kind IN ('shift','vacation','sick')", name="ck_schedule_kind"),
        CheckConstraint("end_at > start_at", name="ck_schedule_range"),
        Index("ix_schedule_park_range", "park_id", "start_at", "end_at"),
        Index("ix_schedule_owner_series_end", "owner_user_id", "series_id", "end_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    start_at: Mapped[datetime] = mapped_column(ScheduleDateTime())
    end_at: Mapped[datetime] = mapped_column(ScheduleDateTime())
    source: Mapped[str] = mapped_column(String(16), default="self")
    series_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint_hash", name="uq_push_endpoint_hash"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64))
    endpoint_encrypted: Mapped[str] = mapped_column(Text)
    p256dh_encrypted: Mapped[str] = mapped_column(Text)
    auth_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    categories_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    system_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")


class NotificationEvent(Base):
    __tablename__ = "notification_events"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(32))
    park_id: Mapped[int | None] = mapped_column(
        ForeignKey("parks.id", ondelete="CASCADE"), nullable=True
    )
    protected_text: Mapped[str] = mapped_column(Text, default="")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrackerNotificationCursor(Base):
    __tablename__ = "tracker_notification_cursors"
    __table_args__ = (
        CheckConstraint(
            "(lease_owner IS NULL AND lease_until IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_tracker_notification_lease_pair",
        ),
        Index("ix_tracker_notification_lease", "lease_until", "scope_key"),
    )

    scope_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    cursor_value: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SystemIncidentOccurrence(Base):
    __tablename__ = "system_incident_occurrences"
    __table_args__ = (
        CheckConstraint("last_seen_at >= started_at", name="ck_system_incident_seen_range"),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= started_at",
            name="ck_system_incident_resolved_range",
        ),
        Index(
            "uq_system_incident_active_key",
            "incident_key",
            unique=True,
            sqlite_where=text("resolved_at IS NULL"),
            postgresql_where=text("resolved_at IS NULL"),
        ),
        Index("ix_system_incident_cleanup", "resolved_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    incident_key: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
