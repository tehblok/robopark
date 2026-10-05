"""Durable, scoped local assistant state; model weights stay outside the database."""

import time
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from robopark_api.models import Base


def new_id():
    return str(uuid4())


class AIConfig(Base):
    __tablename__ = "ai_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    learning_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class AIPrompt(Base):
    __tablename__ = "ai_prompts"
    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class AIDocument(Base):
    __tablename__ = "ai_documents"
    __table_args__ = (Index("ix_ai_documents_source_ref_park_id", "source_ref", "park_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_key: Mapped[str] = mapped_column(String(64), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(250))
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    trust: Mapped[str] = mapped_column(String(20), default="unverified")
    park_id: Mapped[int | None] = mapped_column(
        ForeignKey("parks.id", ondelete="CASCADE"), index=True
    )
    source_ref: Mapped[str] = mapped_column(String(400), default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIBundleDocument(Base):
    """Receipt proving which document revision still matches a shipped source."""

    __tablename__ = "ai_bundle_documents"
    __table_args__ = (UniqueConstraint("document_id", name="uq_ai_bundle_documents_document_id"),)
    source_ref: Mapped[str] = mapped_column(String(400), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("ai_documents.id", ondelete="CASCADE"))
    applied_document_revision: Mapped[int] = mapped_column(Integer)
    applied_signature: Mapped[str] = mapped_column(String(64))
    applied_bundle_revision: Mapped[str] = mapped_column(String(64))
    seen_bundle_revision: Mapped[str] = mapped_column(String(64), index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIChunk(Base):
    __tablename__ = "ai_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("ai_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class AITerm(Base):
    __tablename__ = "ai_terms"
    term: Mapped[str] = mapped_column(String(80), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("ai_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    weight: Mapped[int] = mapped_column(Integer, default=1)


class AIConversation(Base):
    __tablename__ = "ai_conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    issue_key: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200), default="Новый разговор")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIMessage(Base):
    __tablename__ = "ai_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIJob(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_ai_job_key"),
        Index("ix_ai_jobs_due", "state", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE")
    )
    park_id: Mapped[int | None] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(20), default="queued")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIAction(Base):
    """Durable invocation receipt; never automatically replay an uncertain write."""

    __tablename__ = "ai_actions"
    __table_args__ = (
        UniqueConstraint("job_id", "ordinal", name="uq_ai_action_ordinal"),
        Index("ix_ai_actions_state_updated", "state", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_jobs.id", ondelete="SET NULL"), index=True
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    call_id: Mapped[str] = mapped_column(String(128))
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSON)
    expected: Mapped[dict] = mapped_column(JSON)
    preview: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(120))
    expires_at: Mapped[float] = mapped_column(Float)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIConnector(Base):
    __tablename__ = "ai_connectors"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(1000))
    method: Mapped[str] = mapped_column(String(8))
    encrypted_token: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIScript(Base):
    __tablename__ = "ai_scripts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    tested_revision: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIAutomation(Base):
    __tablename__ = "ai_automations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    action: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled_at: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIEvent(Base):
    __tablename__ = "ai_events"
    __table_args__ = (
        Index("ix_ai_events_retention", "processed", "payload_retained", "occurred_at"),
    )
    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    payload_retained: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    occurred_at: Mapped[float] = mapped_column(Float)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class AIRun(Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        UniqueConstraint("automation_id", "event_key", name="uq_ai_rule_event"),
        Index("ix_ai_runs_event_state", "event_key", "state"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    automation_id: Mapped[str] = mapped_column(String(36), index=True)
    event_key: Mapped[str] = mapped_column(String(150))
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(String(120))
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
