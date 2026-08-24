from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from robopark_api.deps import get_mechanic_park, get_user_parks
from robopark_api.models import User, UserRole
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


def enforce_issue_scope(db: Session, user: User, issue: dict) -> None:
    if user.role in {UserRole.admin.value, UserRole.royal.value}:
        return

    issue_queue = str(issue.get("queue") or "").strip()
    issue_key = str(issue.get("key") or "")
    queues = allowed_queues_for_user(db, user)
    if issue_queue and issue_queue not in queues:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tracker_issue_out_of_scope",
        )

    if user.role == UserRole.mechanic.value:
        # Mechanics only work with blockers from their park context.
        if "blocker" not in str(issue.get("summary") or "").lower() and not issue_key:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_issue_out_of_scope",
            )


def ensure_action_allowed(db: Session, user: User, issue: dict, action: str) -> None:
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    enforce_issue_scope(db, user, issue)
    if not can_write_tracker(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tracker_write_disabled",
        )
