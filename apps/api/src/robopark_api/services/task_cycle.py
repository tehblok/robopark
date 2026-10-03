"""Durable boundary between successive repairs of the same Tracker issue."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.task_workflow_models import TaskMessage


def last_confirmed_closure_at(db: Session, issue_key: str) -> float | None:
    """Latest local observation of Tracker's terminal state for this issue."""
    return db.scalar(
        select(TaskMessage.created_at)
        .where(
            TaskMessage.issue_key == issue_key,
            TaskMessage.kind == "system",
            TaskMessage.external_id.like("tracker-external-close:%"),
        )
        .order_by(TaskMessage.created_at.desc())
        .limit(1)
    )
