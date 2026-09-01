from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccessStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(16), default="nav")
    label: Mapped[str] = mapped_column(String(128))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions",
        order_by=Permission.sort_order,
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class UserPermission(Base):
    """Per-user grant/deny on top of the assigned role."""

    __tablename__ = "user_permissions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )
    granted: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"))


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    access_status: Mapped[str] = mapped_column(String(32), default=AccessStatus.approved.value)
    tracker_login: Mapped[str | None] = mapped_column(String(128), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    role_ref: Mapped[Role] = relationship(lazy="joined")
    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user")
    parks: Mapped[list[Park]] = relationship(secondary="user_parks")
    park_requests: Mapped[list[ParkRequest]] = relationship(
        back_populates="user",
        foreign_keys="ParkRequest.user_id",
    )

    @property
    def role(self) -> str:
        return self.role_ref.slug if self.role_ref is not None else ""


class AuthSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

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
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    arrived_count: Mapped[int] = mapped_column(Integer, default=0)
    departed_count: Mapped[int] = mapped_column(Integer, default=0)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    status: Mapped[str] = mapped_column(String(32), default=AccessStatus.pending.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    section_id: Mapped[str] = mapped_column(ForeignKey("emergency_sections.id", ondelete="CASCADE"))
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


class AuditLog(Base):
    """Append-only record of security- and Tracker-relevant actions.

    The platform talks to Tracker through a single service OAuth token, so in
    Tracker every change looks like the same account. Without this table there
    was no way to answer "who closed that ticket".
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    #: Nullable: failed logins are recorded before a user is identified.
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    park_id: Mapped[int | None] = mapped_column(
        ForeignKey("parks.id", ondelete="SET NULL"), nullable=True
    )
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), default="success")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_target_role_status_park_id", "target_role", "status", "park_id"),
        Index("ix_reports_author_user_id_created_at", "author_user_id", "created_at"),
        Index(
            "ix_reports_tracker_key",
            "tracker_key",
            sqlite_where=text("tracker_key IS NOT NULL"),
            postgresql_where=text("tracker_key IS NOT NULL"),
        ),
        Index(
            "uq_reports_open_close_review_park_tracker",
            "park_id",
            "tracker_key",
            unique=True,
            sqlite_where=text("kind = 'ticket_close_review' AND status = 'open'"),
            postgresql_where=text("kind = 'ticket_close_review' AND status = 'open'"),
        ),
        Index(
            "uq_reports_open_emergency_cookie_stale",
            "kind",
            unique=True,
            sqlite_where=text("kind = 'emergency_cookie_stale' AND status = 'open'"),
            postgresql_where=text("kind = 'emergency_cookie_stale' AND status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="open")
    park_id: Mapped[int | None] = mapped_column(ForeignKey("parks.id"), index=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_role: Mapped[str] = mapped_column(String(32), index=True)
    tracker_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tracker_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    parent_report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"), nullable=True)
    return_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attachments: Mapped[list["ReportAttachment"]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        order_by="ReportAttachment.id",
    )


class ReportAttachment(Base):
    __tablename__ = "report_attachments"
    __table_args__ = (
        UniqueConstraint("report_id", "kind", name="uq_report_attachments_report_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("reports.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    report: Mapped[Report] = relationship(back_populates="attachments")
