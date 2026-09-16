"""Local collaboration metadata. No upstream payloads or credentials are stored."""

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from robopark_api.models import Base
from robopark_api.task_workflow_models import ReliableAction


class TrackerPresence(Base):
    __tablename__ = "tracker_presence"
    __table_args__ = (UniqueConstraint("issue_key", "actor_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(128), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[float] = mapped_column(Float, index=True)


# Temporary import compatibility while callers migrate to the generic service.
TrackerSubmission = ReliableAction


class TrackerClaim(Base):
    """Robopark-owned task assignment; Tracker's service account stays the upstream actor."""

    __tablename__ = "tracker_claims"
    issue_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    park_id: Mapped[int] = mapped_column(ForeignKey("parks.id", ondelete="CASCADE"), index=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    updated_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    updated_at: Mapped[float] = mapped_column(Float)


class TrackerHandoff(Base):
    __tablename__ = "tracker_handoffs"
    issue_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[str] = mapped_column(Text, default="")
    remaining: Mapped[str] = mapped_column(Text, default="")
    obstacles: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[float] = mapped_column(Float)
