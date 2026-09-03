from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from robopark_api.models import AccessStatus, Park, Report, Role, User, UserPark
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import rbac
from robopark_api.services.rbac import RoleSlug

KIND_TICKET_QUESTION = "ticket_question"
KIND_TICKET_CLOSE_REVIEW = "ticket_close_review"
KIND_MECHANIC_PROBLEM = "mechanic_problem"
KIND_ESCALATION = "escalation_to_admin"
KIND_EMERGENCY_COOKIE_STALE = "emergency_cookie_stale"

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
    if not _is_approved(author) or not rbac.has_permission(
        db, author, rbac.PERMISSION_REPORTS_CREATE
    ):
        raise PermissionError("forbidden")
    _require_park(db, author, park_id)
    if kind not in MANUAL_KINDS:
        raise ValueError("invalid_report_kind")
    if kind == KIND_TICKET_QUESTION and not tracker_key:
        raise ValueError("tracker_key_required")

    report = Report(
        kind=kind,
        status=STATUS_OPEN,
        park_id=park_id,
        author_user_id=author.id,
        target_role=RoleSlug.OPERATOR,
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
        target_role=RoleSlug.OPERATOR,
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
    query = select(Park.id).where(Park.is_active.is_(True))
    if not rbac.is_admin_or_royal(user):
        query = query.join(UserPark).where(UserPark.user_id == user.id)
    return set(db.scalars(query))


def _is_approved(user: User) -> bool:
    return user.is_active and user.access_status == AccessStatus.approved.value


def _require_park(db: Session, user: User, park_id: int) -> None:
    if db.get(Park, park_id) is None:
        raise LookupError("park not found")
    if park_id not in _user_park_ids(db, user):
        raise PermissionError("forbidden")


def _scope_clause(db: Session, user: User, park_id: int | None = None):
    park_ids = _user_park_ids(db, user)
    if park_id is not None:
        if park_id not in park_ids:
            raise PermissionError("forbidden")
        park_ids = {park_id}
    clause = Report.park_id.in_(park_ids)
    if rbac.is_admin_or_royal(user):
        return or_(clause, Report.park_id.is_(None))
    return clause


def _load_report(db: Session, report_id: int) -> Report:
    report = db.scalar(
        select(Report).options(selectinload(Report.attachments)).where(Report.id == report_id)
    )
    if report is None:
        raise LookupError(f"report not found: {report_id}")
    return report


def _first_admin(db: Session) -> User | None:
    return db.scalars(
        select(User)
        .join(Role)
        .where(
            Role.slug.in_((RoleSlug.ADMIN, RoleSlug.ROYAL)),
            User.is_active.is_(True),
        )
        .order_by(User.id.asc())
    ).first()


def ensure_open_emergency_cookie_report(db: Session, *, author: User | None) -> Report | None:
    existing = db.scalars(
        select(Report).where(
            Report.kind == KIND_EMERGENCY_COOKIE_STALE,
            Report.status == STATUS_OPEN,
        )
    ).first()
    if existing is not None:
        return existing
    user = author or _first_admin(db)
    if user is None:
        return None
    valid = settings_svc.get_emergency_cookie_valid(db)
    updated = None
    row = settings_svc.get_setting(db, settings_svc.EMERGENCY_COOKIE_KEY)
    if row is not None:
        updated = row.updated_at
    body = f"user={user.username}\ncookie_valid={valid}\ncookie_updated_at={updated}"
    report = Report(
        kind=KIND_EMERGENCY_COOKIE_STALE,
        status=STATUS_OPEN,
        park_id=None,
        author_user_id=user.id,
        target_role=RoleSlug.ADMIN,
        title="Emergency cookie протухла",
        body=body,
    )
    db.add(report)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalars(
            select(Report).where(
                Report.kind == KIND_EMERGENCY_COOKIE_STALE,
                Report.status == STATUS_OPEN,
            )
        ).first()
        if existing is None:
            raise
        return existing
    db.refresh(report)
    return report


def resolve_open_emergency_cookie_reports(db: Session) -> int:
    rows = list(
        db.scalars(
            select(Report).where(
                Report.kind == KIND_EMERGENCY_COOKIE_STALE,
                Report.status == STATUS_OPEN,
            )
        ).all()
    )
    now = datetime.now(UTC)
    for report in rows:
        report.status = STATUS_DONE
        report.resolved_at = now
    if rows:
        db.commit()
    return len(rows)


def _is_admin_inbox_user(user: User) -> bool:
    return user.role in (RoleSlug.ADMIN, RoleSlug.ROYAL)


def _is_approved_operator(user: User) -> bool:
    return user.role == RoleSlug.OPERATOR and _is_approved(user)


def _can_resolve(db: Session, user: User) -> bool:
    return _is_approved(user) and rbac.has_permission(db, user, rbac.PERMISSION_REPORTS_RESOLVE)


def _in_scope(db: Session, user: User, report: Report) -> bool:
    if not _is_approved(user):
        return False
    if report.park_id is None:
        return _is_admin_inbox_user(user)
    return report.park_id in _user_park_ids(db, user)


def _can_view_report(db: Session, user: User, report: Report) -> bool:
    if not _in_scope(db, user, report):
        return False
    if report.author_user_id == user.id:
        return True
    if not _can_resolve(db, user):
        return False
    if _is_admin_inbox_user(user):
        return True
    return bool(_is_approved_operator(user) and report.target_role == RoleSlug.OPERATOR)


def _can_act_on_report(db: Session, user: User, report: Report) -> bool:
    if (
        report.status != STATUS_OPEN
        or not _can_resolve(db, user)
        or not _in_scope(db, user, report)
    ):
        return False
    if report.target_role == RoleSlug.ADMIN:
        return _is_admin_inbox_user(user)
    if report.target_role == RoleSlug.OPERATOR:
        return _is_approved_operator(user)
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
        raise ValueError("comment_required")
    return trimmed


def list_inbox(db: Session, user: User, *, park_id: int | None = None) -> list[Report]:
    if not _can_resolve(db, user):
        raise PermissionError("forbidden")
    scope = _scope_clause(db, user, park_id)
    if _is_admin_inbox_user(user):
        stmt = (
            select(Report)
            .options(selectinload(Report.attachments))
            .where(
                Report.status == STATUS_OPEN,
                Report.target_role == RoleSlug.ADMIN,
            )
        )
    elif _is_approved_operator(user):
        stmt = (
            select(Report)
            .options(selectinload(Report.attachments))
            .where(
                Report.status == STATUS_OPEN,
                Report.target_role == RoleSlug.OPERATOR,
            )
        )
    else:
        return []

    return list(
        db.scalars(stmt.where(scope).order_by(Report.created_at.desc(), Report.id.desc())).all()
    )


def list_mine(db: Session, user: User) -> list[Report]:
    if not _is_approved(user):
        raise PermissionError("forbidden")
    return list(
        db.scalars(
            select(Report)
            .options(selectinload(Report.attachments))
            .where(Report.author_user_id == user.id, _scope_clause(db, user))
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
    report.resolved_at = datetime.now(UTC)
    db.commit()
    db.refresh(report)
    return report


def escalate_report(db: Session, user: User, report_id: int, comment: str) -> Report:
    if not _is_approved_operator(user):
        raise PermissionError("forbidden")

    parent = _load_report(db, report_id)
    _require_act(db, user, parent)
    if parent.target_role != RoleSlug.OPERATOR:
        raise PermissionError("forbidden")
    body = _require_non_empty_comment(comment)
    child = Report(
        kind=KIND_ESCALATION,
        status=STATUS_OPEN,
        park_id=parent.park_id,
        author_user_id=user.id,
        target_role=RoleSlug.ADMIN,
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


def badge_counts(db: Session, user: User, *, park_id: int | None = None) -> dict:
    if not _is_approved(user):
        raise PermissionError("forbidden")
    visible = [and_(Report.author_user_id == user.id, Report.status == STATUS_RETURNED)]
    if _can_resolve(db, user):
        if _is_admin_inbox_user(user):
            visible.append(and_(Report.target_role == RoleSlug.ADMIN, Report.status == STATUS_OPEN))
        elif _is_approved_operator(user):
            visible.append(
                and_(Report.target_role == RoleSlug.OPERATOR, Report.status == STATUS_OPEN)
            )
    count = db.scalar(
        select(func.count())
        .select_from(Report)
        .where(
            _scope_clause(db, user, park_id),
            or_(*visible),
        )
    )
    return {"count": count or 0}
