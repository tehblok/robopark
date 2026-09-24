"""Database-backed, payload-free synchronization and worker health."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from robopark_api.ops_schemas import SyncHealthOut
from robopark_api.schedule_models import TrackerNotificationCursor
from robopark_api.task_workflow_models import ReliableAction

WORKER_SCOPE = "worker-runtime"
POLL_SCOPE = "new-tasks"
HEARTBEAT_LEASE = timedelta(seconds=45)
_PENDING_STATES = ("pending", "sending", "retry_wait", "needs_attention")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def record_worker_heartbeat(
    db: Session, *, owner_id: str, now: datetime | None = None
) -> None:
    """Extend the shared lease only for the owning worker process."""
    current = now or datetime.now(UTC)
    row = db.get(TrackerNotificationCursor, WORKER_SCOPE)
    if row is None:
        row = TrackerNotificationCursor(scope_key=WORKER_SCOPE)
        db.add(row)
    row.lease_owner = owner_id
    row.lease_until = current + HEARTBEAT_LEASE
    row.last_success_at = current
    row.last_error = None
    db.commit()


def release_worker_heartbeat(db: Session, *, owner_id: str) -> None:
    row = db.get(TrackerNotificationCursor, WORKER_SCOPE)
    if row is not None and row.lease_owner == owner_id:
        row.lease_owner = None
        row.lease_until = None
        db.commit()


def sync_health(db: Session, *, now: datetime | None = None) -> SyncHealthOut:
    """Project only finite counters, age, and stable error codes for admin/royal."""
    current = now or datetime.now(UTC)
    poll = db.get(TrackerNotificationCursor, POLL_SCOPE)
    worker = db.get(TrackerNotificationCursor, WORKER_SCOPE)
    pending_count, oldest = db.execute(
        select(func.count(ReliableAction.id), func.min(ReliableAction.created_at)).where(
            ReliableAction.state.in_(_PENDING_STATES)
        )
    ).one()
    retry_count = db.scalar(
        select(func.count(ReliableAction.id)).where(ReliableAction.state == "retry_wait")
    ) or 0
    needs_attention_count = db.scalar(
        select(func.count(ReliableAction.id)).where(ReliableAction.state == "needs_attention")
    ) or 0
    worker_state = "unknown"
    if worker is not None:
        worker_state = (
            "active"
            if worker.lease_until is not None and _aware(worker.lease_until) > current
            else "stale"
        )
    return SyncHealthOut(
        cursor_age_seconds=(
            max(0, int((current - _aware(poll.last_success_at)).total_seconds()))
            if poll is not None and poll.last_success_at is not None
            else None
        ),
        pending_action_count=pending_count,
        oldest_pending_action_age_seconds=(
            max(0, int(current.timestamp() - oldest)) if oldest is not None else None
        ),
        retry_count=retry_count,
        needs_attention_count=needs_attention_count,
        last_success_at=poll.last_success_at if poll is not None else None,
        last_error=(
            poll.last_error
            if poll is not None and poll.last_error in {"tracker_unavailable", "poll_failed"}
            else None
        ),
        worker_lease_state=worker_state,
    )
