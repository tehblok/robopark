"""Local collaboration metadata. No upstream payloads or credentials are stored."""

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from robopark_api.models import Base


class TrackerPresence(Base):
    __tablename__ = "tracker_presence"
    __table_args__ = (UniqueConstraint("issue_key", "actor_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(128), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class TrackerSubmission(Base):
    __tablename__ = "tracker_submissions"
    __table_args__ = (UniqueConstraint("actor_id", "issue_key", "action", "request_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    issue_key: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(32))
    request_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)


class TrackerHandoff(Base):
    __tablename__ = "tracker_handoffs"
    issue_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[str] = mapped_column(Text, default="")
    remaining: Mapped[str] = mapped_column(Text, default="")
    obstacles: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[float] = mapped_column(Float)
