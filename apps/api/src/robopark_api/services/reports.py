from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import Report, User, UserRole

KIND_TICKET_QUESTION = "ticket_question"
KIND_TICKET_CLOSE_REVIEW = "ticket_close_review"
KIND_MECHANIC_PROBLEM = "mechanic_problem"
KIND_ESCALATION = "escalation_to_admin"

STATUS_OPEN = "open"
STATUS_RETURNED = "returned"
STATUS_DONE = "done"

MANUAL_KINDS = frozenset({KIND_TICKET_QUESTION, KIND_MECHANIC_PROBLEM})


def create_manual_report(
    db: Session,
    *,
    author: User,
    park_id: int,
    kind: str,
    title: str,
    body: str,
    tracker_key: str | None,
    tracker_url: str | None,
) -> Report:
    if kind not in MANUAL_KINDS:
        raise ValueError(f"invalid manual report kind: {kind}")
    if kind == KIND_TICKET_QUESTION and not tracker_key:
        raise ValueError("tracker_key is required for ticket_question")

    report = Report(
        kind=kind,
        status=STATUS_OPEN,
        park_id=park_id,
        author_user_id=author.id,
        target_role=UserRole.operator.value,
        tracker_key=tracker_key,
        tracker_url=tracker_url,
        title=title,
        body=body,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def get_or_create_close_review(
    db: Session,
    *,
    author: User,
    park_id: int,
    tracker_key: str,
    tracker_url: str | None,
    title: str,
    body: str = "",
) -> Report:
    existing = db.scalars(
        select(Report).where(
            Report.park_id == park_id,
            Report.tracker_key == tracker_key,
            Report.kind == KIND_TICKET_CLOSE_REVIEW,
            Report.status == STATUS_OPEN,
        )
    ).first()
    if existing is not None:
        return existing

    report = Report(
        kind=KIND_TICKET_CLOSE_REVIEW,
        status=STATUS_OPEN,
        park_id=park_id,
        author_user_id=author.id,
        target_role=UserRole.operator.value,
        tracker_key=tracker_key,
        tracker_url=tracker_url,
        title=title,
        body=body,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report
