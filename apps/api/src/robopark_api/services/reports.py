from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from robopark_api.models import (
    AccessStatus,
    AuditLog,
    CampaignSubmission,
    Park,
    Report,
    Role,
    User,
    UserPark,
)
from robopark_api.services import audit, rbac, report_attachments
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.rbac import RoleSlug

logger = logging.getLogger(__name__)

KIND_TICKET_QUESTION = "ticket_question"
KIND_TICKET_CLOSE_REVIEW = "ticket_close_review"
KIND_MECHANIC_PROBLEM = "mechanic_problem"
KIND_ESCALATION = "escalation_to_admin"
KIND_EMERGENCY_COOKIE_STALE = "emergency_cookie_stale"

STATUS_OPEN = "open"
STATUS_RETURNED = "returned"
STATUS_DONE = "done"

MANUAL_KINDS = frozenset({KIND_TICKET_QUESTION, KIND_MECHANIC_PROBLEM})


class ReportLinkedError(ValueError):
    """A completed campaign still depends on this report."""


NotificationHook = Callable[[Session, Report], None]


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
    notification_hook: NotificationHook | None = None,
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
    db.flush()
    if notification_hook is not None:
        notification_hook(db, report)
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


def ensure_open_emergency_cookie_report(
    db: Session,
    *,
    author: User | None,
    expected_identity: str | None = None,
) -> Report | None:
    guarded = expected_identity is not None
    if guarded and not settings_svc.claim_emergency_cookie_identity(db, expected_identity):
        db.rollback()
        return None
    existing = db.scalars(
        select(Report).where(
            Report.kind == KIND_EMERGENCY_COOKIE_STALE,
            Report.status == STATUS_OPEN,
        )
    ).first()
    if existing is not None:
        if guarded:
            db.commit()
        return existing
    user = author or _first_admin(db)
    if user is None:
        if guarded:
            db.commit()
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


def resolve_open_emergency_cookie_reports(
    db: Session,
    *,
    expected_identity: str | None = None,
) -> int:
    guarded = expected_identity is not None
    if guarded and not settings_svc.claim_emergency_cookie_identity(db, expected_identity):
        db.rollback()
        return 0
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
    if rows or guarded:
        db.commit()
    return len(rows)


def _is_admin_inbox_user(user: User) -> bool:
    return user.role in (RoleSlug.ADMIN, RoleSlug.ROYAL)


def _is_royal_inbox_user(user: User) -> bool:
    return user.role == RoleSlug.ROYAL


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
        return _is_approved_operator(user) or _is_admin_inbox_user(user)
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
        stmt = select(Report).options(selectinload(Report.attachments))
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


def delete_report(db: Session, user: User, report_id: int) -> None:
    """Remove an ordinary report; preserve linked campaign results by refusing deletion."""
    if not _is_approved(user) or not rbac.is_admin_or_royal(user):
        raise PermissionError("forbidden")
    report = _load_report(db, report_id)
    if not _in_scope(db, user, report):
        raise PermissionError("forbidden")
    if db.scalar(select(CampaignSubmission.id).where(CampaignSubmission.report_id == report_id)):
        raise ReportLinkedError("report_linked_to_campaign")
    park_id = report.park_id
    storage_keys = [attachment.storage_key for attachment in report.attachments]
    try:
        if storage_keys:
            report_attachments.mark_report_files_for_deletion(report_id, storage_keys)
        db.execute(
            update(Report).where(Report.parent_report_id == report_id).values(parent_report_id=None)
        )
        db.delete(report)
        db.add(
            AuditLog(
                action="reports.delete",
                actor_user_id=user.id,
                actor_username=user.username,
                actor_role=user.role,
                park_id=park_id,
                target_type="report",
                target_id=str(report_id),
                outcome=audit.OUTCOME_SUCCESS,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        report_attachments.clear_report_delete_marker(report_id)
        raise
    if storage_keys:
        try:
            report_attachments.reconcile_pending_report_deletions(db)
        except Exception:
            # The DB deletion has committed. The durable marker lets cleanup
            # retry without incorrectly reporting the user action as failed.
            logger.exception("Report %s deleted; attachment cleanup pending", report_id)


def return_report(
    db: Session,
    user: User,
    report_id: int,
    comment: str,
    *,
    notification_hook: NotificationHook | None = None,
) -> Report:
    report = _load_report(db, report_id)
    _require_act(db, user, report)
    report.status = STATUS_RETURNED
    report.return_comment = _require_non_empty_comment(comment)
    if notification_hook is not None:
        notification_hook(db, report)
    db.commit()
    db.refresh(report)
    return report


def resubmit_report(
    db: Session,
    user: User,
    report_id: int,
    *,
    title: str,
    body: str,
    tracker_key: str | None,
    tracker_url: str | None,
    notification_hook: NotificationHook | None = None,
) -> Report:
    report = _load_report(db, report_id)
    if (
        not _is_approved(user)
        or report.author_user_id != user.id
        or report.status != STATUS_RETURNED
        or not _in_scope(db, user, report)
    ):
        raise PermissionError("forbidden")
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("title_required")
    clean_key = (tracker_key or "").strip() or None
    if report.kind == KIND_TICKET_QUESTION and not clean_key:
        raise ValueError("tracker_key_required")
    report.title = clean_title
    report.body = body.strip()
    report.tracker_key = clean_key
    report.tracker_url = (tracker_url or "").strip() or None
    report.return_comment = None
    report.status = STATUS_OPEN
    report.resolved_at = None
    if notification_hook is not None:
        notification_hook(db, report)
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
            visible.append(Report.status == STATUS_OPEN)
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
