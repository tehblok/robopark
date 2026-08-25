from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.deps import get_mechanic_park, get_user_parks
from robopark_api.models import Park, User, UserRole
from robopark_api.services import platform_settings as settings_svc

ALLOWED_ACTIONS = {"comment", "assign", "unassign", "transition", "close"}


def allowed_queues_for_user(db: Session, user: User) -> list[str]:
    if user.role in {UserRole.admin.value, UserRole.royal.value}:
        return []
    if user.role == UserRole.operator.value:
        parks = get_user_parks(db, user)
        seen: set[str] = set()
        queues: list[str] = []
        for park in parks:
            queue = (park.tracker_queue or "").strip()
            if queue and queue not in seen:
                seen.add(queue)
                queues.append(queue)
        return queues
    if user.role == UserRole.mechanic.value:
        park = get_mechanic_park(db, user)
        if park is None:
            return []
        queue = (park.tracker_queue or "").strip()
        return [queue] if queue else []
    return []


def all_park_tags(db: Session) -> set[str]:
    """Tags of every park known to the platform, used to detect foreign-park issues."""
    rows = db.scalars(select(Park.tag)).all()
    return {str(tag).strip() for tag in rows if tag and str(tag).strip()}


def allowed_park_tags_for_user(db: Session, user: User) -> set[str]:
    if user.role in {UserRole.admin.value, UserRole.royal.value}:
        return set()
    if user.role == UserRole.operator.value:
        return {str(park.tag).strip() for park in get_user_parks(db, user) if park.tag}
    if user.role == UserRole.mechanic.value:
        park = get_mechanic_park(db, user)
        if park and park.tag:
            return {str(park.tag).strip()}
    return set()


def can_view_untagged(db: Session, user: User) -> bool:
    if user.role in {UserRole.admin.value, UserRole.royal.value}:
        return True
    if user.role == UserRole.operator.value:
        return settings_svc.tracker_policy_status(db)["operator_show_untagged"]
    return False


def can_write_tracker(db: Session, user: User) -> bool:
    if user.role in {UserRole.admin.value, UserRole.royal.value, UserRole.operator.value}:
        return True
    if user.role == UserRole.mechanic.value:
        return settings_svc.tracker_policy_status(db)["mechanic_can_write"]
    return False


class IssueOutOfScope(Exception):
    """Raised internally when an issue is not provably inside the user's scope."""


def _deny() -> None:
    raise IssueOutOfScope


def issue_tags(issue: dict) -> set[str]:
    raw = issue.get("tags") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(tag).strip() for tag in raw if str(tag).strip()}


def is_issue_in_scope(db: Session, user: User, issue: dict) -> bool:
    """Non-raising scope check, used to filter list responses."""
    try:
        _check_issue_scope(db, user, issue)
    except IssueOutOfScope:
        return False
    return True


def enforce_issue_scope(db: Session, user: User, issue: dict) -> None:
    """Fail-closed scope check for single-issue access; raises 403 when unverifiable."""
    try:
        _check_issue_scope(db, user, issue)
    except IssueOutOfScope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tracker_issue_out_of_scope",
        ) from None


def _check_issue_scope(db: Session, user: User, issue: dict) -> None:
    """Fail-closed scope check: an issue must provably belong to the user's parks.

    Anything unverifiable (missing queue, unknown tag, no park assigned) is denied
    for non-admin roles instead of being silently allowed.
    """
    if user.role in {UserRole.admin.value, UserRole.royal.value}:
        return

    if user.role not in {UserRole.operator.value, UserRole.mechanic.value}:
        _deny()

    # 1. Queue must be present and inside the user's allowed set.
    issue_queue = str(issue.get("queue") or "").strip()
    queues = allowed_queues_for_user(db, user)
    if not issue_queue or not queues or issue_queue not in queues:
        _deny()

    # 2. Park scope by tag.
    allowed_tags = allowed_park_tags_for_user(db, user)
    tags = issue_tags(issue)

    if tags & allowed_tags:
        return

    # Tags belonging to a different park are always out of scope.
    if tags & (all_park_tags(db) - allowed_tags):
        _deny()

    # No park tag at all (or only non-park tags like "donor"): this is an
    # «untagged» issue, reachable only when the policy allows it.
    if not can_view_untagged(db, user):
        _deny()


def ensure_action_allowed(db: Session, user: User, issue: dict, action: str) -> None:
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    enforce_issue_scope(db, user, issue)
    if not can_write_tracker(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tracker_write_disabled",
        )
