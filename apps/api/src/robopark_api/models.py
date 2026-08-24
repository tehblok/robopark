from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRole(StrEnum):
    royal = "royal"
    admin = "admin"
    operator = "operator"
    mechanic = "mechanic"


class AccessStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    access_status: Mapped[str] = mapped_column(
        String(32), default=AccessStatus.approved.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user")
    parks: Mapped[list[Park]] = relationship(secondary="user_parks")
    park_requests: Mapped[list[ParkRequest]] = relationship(
        back_populates="user",
        foreign_keys="ParkRequest.user_id",
    )


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class Park(Base):
    __tablename__ = "parks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    tag: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    tracker_queue: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tracker_priority: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracker_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_blockers: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_sla_repair: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_backlog_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    blocker_history: Mapped[list[ParkBlockerHistory]] = relationship(
        back_populates="park",
        cascade="all, delete-orphan",
    )


class ParkBlockerHistory(Base):
    __tablename__ = "park_blocker_history"
    __table_args__ = (
        UniqueConstraint(
            "park_id",
            "bucket_start",
            name="uq_park_blocker_history_park_bucket",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    park_id: Mapped[int] = mapped_column(
        ForeignKey("parks.id", ondelete="CASCADE"), index=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    arrived_count: Mapped[int] = mapped_column(Integer, default=0)
    departed_count: Mapped[int] = mapped_column(Integer, default=0)
    scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    park: Mapped[Park] = relationship(back_populates="blocker_history")


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserPark(Base):
    __tablename__ = "user_parks"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    park_id: Mapped[int] = mapped_column(
        ForeignKey("parks.id", ondelete="CASCADE"), primary_key=True
    )


class ParkRequest(Base):
    __tablename__ = "park_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(
        String(32), default=AccessStatus.pending.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    user: Mapped[User] = relationship(
        back_populates="park_requests",
        foreign_keys=[user_id],
    )


class EmergencySection(Base):
    __tablename__ = "emergency_sections"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    formatter: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    fields: Mapped[list[EmergencyField]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )
    roles: Mapped[list[EmergencySectionRole]] = relationship(
        back_populates="section", cascade="all, delete-orphan"
    )


class EmergencyField(Base):
    __tablename__ = "emergency_fields"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    section_id: Mapped[str] = mapped_column(
        ForeignKey("emergency_sections.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(256))
    label: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    section: Mapped[EmergencySection] = relationship(back_populates="fields")


class EmergencySectionRole(Base):
    __tablename__ = "emergency_section_roles"

    section_id: Mapped[str] = mapped_column(
        ForeignKey("emergency_sections.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True)

    section: Mapped[EmergencySection] = relationship(back_populates="roles")
