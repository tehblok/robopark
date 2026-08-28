"""Audit trail for security- and Tracker-relevant actions.

Every write to Tracker is performed with one platform-wide service token, so
Tracker itself cannot tell operators and mechanics apart. This module records
who did what, from where, and whether it succeeded.

Auditing must never break the operation it observes: :func:`record` swallows
its own errors and only logs them.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from robopark_api.models import AuditLog, User

logger = logging.getLogger(__name__)

# Authentication
ACTION_LOGIN_SUCCESS = "auth.login.success"
ACTION_LOGIN_FAILED = "auth.login.failed"
ACTION_LOGIN_BLOCKED = "auth.login.blocked"
ACTION_LOGOUT = "auth.logout"
ACTION_REGISTER = "auth.register"

# Tracker writes
ACTION_TRACKER_COMMENT = "tracker.comment"
ACTION_TRACKER_ASSIGN = "tracker.assign"
ACTION_TRACKER_UNASSIGN = "tracker.unassign"
ACTION_TRACKER_TRANSITION = "tracker.transition"
ACTION_TRACKER_CLOSE = "tracker.close"
ACTION_TRACKER_ATTACH = "tracker.attach"

# Administration
ACTION_SETTINGS_CHANGED = "admin.settings.changed"
ACTION_MECHANIC_CREATED = "admin.mechanic.created"
ACTION_MECHANIC_UPDATED = "admin.mechanic.updated"
ACTION_ACCESS_APPROVED = "admin.access.approved"
ACTION_ACCESS_REJECTED = "admin.access.rejected"

TRACKER_ACTIONS = {
    "comment": ACTION_TRACKER_COMMENT,
    "assign": ACTION_TRACKER_ASSIGN,
    "unassign": ACTION_TRACKER_UNASSIGN,
    "transition": ACTION_TRACKER_TRANSITION,
    "close": ACTION_TRACKER_CLOSE,
    "attach": ACTION_TRACKER_ATTACH,
}

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_DENIED = "denied"


def record(
    db: Session,
    *,
    action: str,
    actor: User | None = None,
    actor_username: str | None = None,
    park_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = OUTCOME_SUCCESS,
    detail: str | None = None,
    client_ip: str | None = None,
) -> None:
    """Append an audit entry. Never raises."""
    try:
        entry = AuditLog(
            action=action,
            actor_user_id=actor.id if actor is not None else None,
            actor_username=actor.username if actor is not None else actor_username,
            actor_role=actor.role if actor is not None else None,
            park_id=park_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            outcome=outcome,
            detail=detail,
            client_ip=client_ip,
        )
        db.add(entry)
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to write audit entry for action %r", action)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            logger.exception("Audit rollback failed")


def list_entries(
    db: Session,
    *,
    action: str | None = None,
    actor_user_id: int | None = None,
    target_id: str | None = None,
    park_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    """Return a page of audit entries, newest first, plus the total count."""
    from sqlalchemy import func

    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if actor_user_id is not None:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if target_id:
        filters.append(AuditLog.target_id == target_id)
    if park_id is not None:
        filters.append(AuditLog.park_id == park_id)

    total = db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    rows = db.scalars(
        select(AuditLog)
        .where(*filters)
        .order_by(desc(AuditLog.created_at), desc(AuditLog.id))
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), int(total)


def signature_for(user: User) -> str:
    """Attribution footer appended to comments written through the platform."""
    role_labels = {
        "mechanic": "механик",
        "operator": "оператор",
        "admin": "админ",
        "royal": "админ",
    }
    label = role_labels.get(user.role, user.role)
    return f"\n\n—\nРобопарк: {user.username} ({label})"


def describe(payload: Any, limit: int = 500) -> str:
    """Short, safe text representation for the audit detail column."""
    text = str(payload)
    return text if len(text) <= limit else f"{text[:limit]}…"
