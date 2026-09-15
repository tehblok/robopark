"""Durable local state for task workflows and Tracker delivery."""

import time
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym, validates

from robopark_api.models import Base


class ReliableAction(Base):
    __tablename__ = "reliable_actions"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id",
            "resource_type",
            "resource_id",
            "action",
            "idempotency_key",
            name="uq_reliable_actions_idempotency_scope",
        ),
        CheckConstraint(
            "state IN ('pending', 'sending', 'succeeded', 'retry_wait', 'needs_attention')",
            name="ck_reliable_actions_state",
        ),
        Index("ix_reliable_actions_due", "state", "next_attempt_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    resource_type: Mapped[str] = mapped_column(String(64), default="tracker_issue")
    resource_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    state: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)

    # Kept for one release so the legacy tracker_submissions service remains importable
    # while Task 2 migrates its implementation to generic resource/action names.
    actor_id = synonym("actor_user_id")
    issue_key = synonym("resource_id")
    request_key = synonym("idempotency_key")

    @validates("state")
    def _normalize_legacy_uncertain_state(self, _key: str, value: str) -> str:
        return "needs_attention" if value == "uncertain" else value


class TaskMessage(Base):
    __tablename__ = "task_messages"
    __table_args__ = (
        UniqueConstraint("issue_key", "kind", "external_id", name="uq_task_messages_external_id"),
        CheckConstraint("kind IN ('user', 'system', 'tracker')", name="ck_task_messages_kind"),
        CheckConstraint(
            "sync_state IN ('saved', 'pending', 'synced', 'needs_attention')",
            name="ck_task_messages_sync_state",
        ),
        Index("ix_task_messages_issue_created", "issue_key", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    author_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action_id: Mapped[str | None] = mapped_column(
        ForeignKey("reliable_actions.id", ondelete="SET NULL"), nullable=True
    )
    sync_state: Mapped[str] = mapped_column(String(32), default="saved", server_default="saved")
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)


class TaskAttachment(Base):
    __tablename__ = "task_attachments"
    __table_args__ = (Index("ix_task_attachments_message_id", "message_id", "id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("task_messages.id", ondelete="CASCADE"))
    blob_name: Mapped[str] = mapped_column(String(256))
    original_name: Mapped[str] = mapped_column(String(256))
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float)
    uploaded_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class TaskReview(Base):
    __tablename__ = "task_reviews"
    __table_args__ = (
        CheckConstraint("state IN ('pending', 'returned', 'closed')", name="ck_task_reviews_state"),
        Index(
            "uq_task_reviews_open_issue",
            "issue_key",
            unique=True,
            sqlite_where=text("state != 'closed'"),
            postgresql_where=text("state != 'closed'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewer_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    return_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)
    closed_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class HiddenTask(Base):
    __tablename__ = "hidden_tasks"
    __table_args__ = (
        Index(
            "uq_hidden_tasks_active_issue",
            "issue_key",
            unique=True,
            sqlite_where=text("restored_at IS NULL"),
            postgresql_where=text("restored_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(128), index=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    restored_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)
    restored_at: Mapped[float | None] = mapped_column(Float, nullable=True)
