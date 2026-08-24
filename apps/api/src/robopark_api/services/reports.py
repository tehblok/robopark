from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from robopark_api.deps import get_user_parks
from robopark_api.models import AccessStatus, Report, User, UserRole

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


def _user_park_ids(db: Session, user: User) -> set[int]:
    return {park.id for park in get_user_parks(db, user)}


def _load_report(db: Session, report_id: int) -> Report:
    report = db.get(Report, report_id)
    if report is None:
        raise LookupError(f"report not found: {report_id}")
    return report


def _is_admin_inbox_user(user: User) -> bool:
    return user.role in (UserRole.admin.value, UserRole.royal.value)


def _is_approved_operator(user: User) -> bool:
    return (
        user.role == UserRole.operator.value
        and user.access_status == AccessStatus.approved.value
    )


def _can_view_report(db: Session, user: User, report: Report) -> bool:
    if report.author_user_id == user.id:
        return True
    if _is_admin_inbox_user(user):
        return True
    if (
        _is_approved_operator(user)
        and report.target_role == UserRole.operator.value
        and report.park_id in _user_park_ids(db, user)
    ):
        return True
    return False


def _can_act_on_report(db: Session, user: User, report: Report) -> bool:
    if report.status != STATUS_OPEN:
        return False
    if report.target_role == UserRole.admin.value:
        return _is_admin_inbox_user(user)
    if report.target_role == UserRole.operator.value:
        return (
            _is_approved_operator(user)
            and report.park_id in _user_park_ids(db, user)
        )
    return False


def _require_view(db: Session, user: User, report: Report) -> None:
    if not _can_view_report(db, user, report):
        raise PermissionError("forbidden")


def _require_act(db: Session, user: User, report: Report) -> None:
    if not _can_act_on_report(db, user, report):
        raise PermissionError("forbidden")


def _require_non_empty_comment(comment: str) -> str:
    trimmed = comment.strip()
    if not trimmed:
        raise ValueError("comment is required")
    return trimmed


def list_inbox(db: Session, user: User, *, park_id: int | None = None) -> list[Report]:
    if _is_admin_inbox_user(user):
        stmt = select(Report).where(
            Report.status == STATUS_OPEN,
            Report.target_role == UserRole.admin.value,
        )
    elif _is_approved_operator(user):
        park_ids = _user_park_ids(db, user)
        if park_id is not None:
            if park_id not in park_ids:
                raise PermissionError("forbidden")
            park_ids = {park_id}
        if not park_ids:
            return []
        stmt = select(Report).where(
            Report.status == STATUS_OPEN,
            Report.target_role == UserRole.operator.value,
            Report.park_id.in_(park_ids),
        )
    else:
        return []

    if park_id is not None and _is_admin_inbox_user(user):
        stmt = stmt.where(Report.park_id == park_id)

    return list(
        db.scalars(stmt.order_by(Report.created_at.desc(), Report.id.desc())).all()
    )


def list_mine(db: Session, user: User) -> list[Report]:
    return list(
        db.scalars(
            select(Report)
            .where(Report.author_user_id == user.id)
            .order_by(Report.created_at.desc(), Report.id.desc())
        ).all()
    )


def get_report(db: Session, user: User, report_id: int) -> Report:
    report = _load_report(db, report_id)
    _require_view(db, user, report)
    return report


def return_report(db: Session, user: User, report_id: int, comment: str) -> Report:
    report = _load_report(db, report_id)
    _require_act(db, user, report)
    report.status = STATUS_RETURNED
    report.return_comment = _require_non_empty_comment(comment)
    db.commit()
    db.refresh(report)
    return report


def done_report(db: Session, user: User, report_id: int) -> Report:
    report = _load_report(db, report_id)
    _require_act(db, user, report)
    report.status = STATUS_DONE
    report.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(report)
    return report


def escalate_report(db: Session, user: User, report_id: int, comment: str) -> Report:
    if not _is_approved_operator(user):
        raise PermissionError("forbidden")

    parent = _load_report(db, report_id)
    if parent.status != STATUS_OPEN:
        raise PermissionError("forbidden")
    if parent.target_role != UserRole.operator.value:
        raise PermissionError("forbidden")
    if parent.park_id not in _user_park_ids(db, user):
        raise PermissionError("forbidden")

    body = _require_non_empty_comment(comment)
    child = Report(
        kind=KIND_ESCALATION,
        status=STATUS_OPEN,
        park_id=parent.park_id,
        author_user_id=user.id,
        target_role=UserRole.admin.value,
        tracker_key=parent.tracker_key,
        tracker_url=parent.tracker_url,
        title=parent.title,
        body=body,
        parent_report_id=parent.id,
    )
    db.add(child)
    db.commit()
    db.refresh(child)
    return child


def badge_counts(db: Session, user: User) -> dict:
    if user.role == UserRole.mechanic.value:
        count = db.scalar(
            select(func.count())
            .select_from(Report)
            .where(
                Report.author_user_id == user.id,
                Report.status == STATUS_RETURNED,
            )
        )
        return {"count": count or 0}

    if _is_admin_inbox_user(user):
        count = db.scalar(
            select(func.count())
            .select_from(Report)
            .where(
                Report.status == STATUS_OPEN,
                Report.target_role == UserRole.admin.value,
            )
        )
        return {"count": count or 0}

    if _is_approved_operator(user):
        park_ids = _user_park_ids(db, user)
        if not park_ids:
            return {"count": 0}
        count = db.scalar(
            select(func.count())
            .select_from(Report)
            .where(
                Report.status == STATUS_OPEN,
                Report.target_role == UserRole.operator.value,
                Report.park_id.in_(park_ids),
            )
        )
        return {"count": count or 0}

    return {"count": 0}
