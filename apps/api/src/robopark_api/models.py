from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
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
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feature_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_blockers: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_sla_repair: Mapped[bool] = mapped_column(Boolean, default=True)
    feature_backlog_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


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
