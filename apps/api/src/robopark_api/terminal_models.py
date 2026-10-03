"""Lifecycle metadata only; terminal input/output and raw tickets never persist."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from robopark_api.models import Base


class TerminalSession(Base):
    __tablename__ = "terminal_sessions"
    __table_args__ = (
        CheckConstraint("profile IN ('maintenance','root')", name="ck_terminal_profile"),
        CheckConstraint(
            "state IN ('starting','detached','active','ended')", name="ck_terminal_state"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[int] = mapped_column(Integer, index=True)
    auth_session_hash: Mapped[str] = mapped_column(String(64), index=True)
    profile: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default="starting")
    credential_generation: Mapped[int] = mapped_column(Integer)
    broker_epoch: Mapped[str] = mapped_column(String(36))
    descriptor: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    termination_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attachment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attachment_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    input_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    output_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class TerminalAttachTicket(Base):
    __tablename__ = "terminal_attach_tickets"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("terminal_sessions.id", ondelete="CASCADE"), index=True
    )
    auth_session_hash: Mapped[str] = mapped_column(String(64))
    broker_epoch: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TerminalSessionEvent(Base):
    __tablename__ = "terminal_session_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("terminal_sessions.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
