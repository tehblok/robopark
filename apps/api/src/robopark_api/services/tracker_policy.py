from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.deps import get_user_parks
from robopark_api.models import Park, User
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import rbac, tracker_client
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.tracker_filters import status_bucket

ALLOWED_ACTIONS = {"comment", "assign", "unassign", "transition", "close", "attach"}


def allowed_queues_for_user(db: Session, user: User) -> list[str]:
    if rbac.is_admin_or_royal(user):
        return []
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ) or rbac.has_permission(
        db, user, rbac.PERMISSION_TRACKER_WRITE
    ):
        parks = get_user_parks(db, user)
        seen: set[str] = set()
        queues: list[str] = []
        for park in parks:
            queue = (park.tracker_queue or "").strip()
            if queue and queue not in seen:
                seen.add(queue)
                queues.append(queue)
        return queues
    return []


def all_park_tags(db: Session) -> set[str]:
    """Tags of every park known to the platform, used to detect foreign-park issues."""
    rows = db.scalars(select(Park.tag)).all()
    return {str(tag).strip() for tag in rows if tag and str(tag).strip()}


def allowed_park_tags_for_user(db: Session, user: User) -> set[str]:
    if rbac.is_admin_or_royal(user):
        return set()
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ) or rbac.has_permission(
        db, user, rbac.PERMISSION_TRACKER_WRITE
    ):
        return {str(park.tag).strip() for park in get_user_parks(db, user) if park.tag}
    return set()


def can_view_untagged(db: Session, user: User) -> bool:
    if rbac.is_admin_or_royal(user):
        return True
    if rbac.role_slug(user) == RoleSlug.OPERATOR:
        return settings_svc.tracker_policy_status(db)["operator_show_untagged"]
    return False


def can_write_tracker(db: Session, user: User) -> bool:
    if not rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_WRITE):
        return False
    if rbac.role_slug(user) == RoleSlug.MECHANIC:
        return settings_svc.tracker_policy_status(db)["mechanic_can_write"]
    return True


class IssueOutOfScope(Exception):
    """Raised internally when an issue is not provably inside the user's scope."""


def _deny() -> None:
    raise IssueOutOfScope


def issue_tags(issue: dict) -> set[str]:
    raw = issue.get("tags") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(tag).strip() for tag in raw if str(tag).strip()}


def park_tag_identity(tag: str) -> str:
    return tag.strip().casefold()


def park_tag_matches(tag: str, tags) -> bool:
    identity = park_tag_identity(tag)
    return bool(identity) and any(park_tag_identity(value) == identity for value in tags)


def _scope_tags(tags: set[str] | frozenset[str]) -> set[str]:
    """Normalize Tracker tag identity without changing values used in QL."""
    return {park_tag_identity(tag) for tag in tags}


def issue_authorization_status(issue: dict) -> str | None:
    """Canonical workflow status for access decisions; no UI/relocation hints."""
    return status_bucket(str(issue.get("status_key") or ""), str(issue.get("status") or ""))


def is_issue_status_visible(user: User, issue: dict) -> bool:
    """Driver authorization uses exact workflow status, never relocation hints."""
    return rbac.role_slug(user) != RoleSlug.DRIVER or issue_authorization_status(issue) in {
        "new",
        "moving",
    }


@dataclass(frozen=True)
class IssueScope:
    """One response's policy inputs; never stored in a shared or session cache."""

    queues: frozenset[str]
    allowed_tags: frozenset[str]
    park_tags: frozenset[str]
    view_untagged: bool


@dataclass(frozen=True)
class RelatedRobotScope:
    park_queues: frozenset[tuple[str, str]]
    park_tags: frozenset[str]

    @property
    def queues(self) -> frozenset[str]:
        return frozenset(queue for queue, _tag in self.park_queues)


def load_related_robot_scope(db: Session, user: User, selected_park=None) -> RelatedRobotScope:
    parks = (
        db.scalars(select(Park).where(Park.is_active.is_(True))).all()
        if rbac.role_slug(user) == RoleSlug.ROYAL
        else get_user_parks(db, user)
    )
    return RelatedRobotScope(
        frozenset(
            ((park.tracker_queue or "").strip().upper(), park_tag_identity(park.tag))
            for park in parks
            if park.is_active
            and park.tracker_queue
            and park.tag
            and (not selected_park or park_tag_matches(selected_park, {park.tag}))
        ),
        frozenset(_scope_tags(all_park_tags(db))),
    )


def _related_queue(issue: dict) -> str:
    return str(issue.get("queue") or str(issue.get("key") or "").rsplit("-", 1)[0]).strip().upper()


def related_robot_anchors(db, user, issues, robot, selected_park=None, scope=None) -> set[str]:
    scope = scope or load_related_robot_scope(db, user, selected_park)
    return {
        _related_queue(issue)
        for issue in issues
        if robot in tracker_client.issue_robot_numbers(issue)
        and tracker_client.is_issue_open_item(issue)
        and is_issue_status_visible(user, issue)
        and any(
            (_related_queue(issue), tag) in scope.park_queues
            for tag in _scope_tags(issue_tags(issue))
        )
    }


def is_related_robot_issue_in_scope(
    db, user, issue, robot, anchor_queues, selected_park=None, scope=None
) -> bool:
    scope = scope or load_related_robot_scope(db, user, selected_park)
    queue = _related_queue(issue)
    if queue not in scope.queues or robot not in tracker_client.issue_robot_numbers(issue):
        return False
    if not is_issue_status_visible(user, issue):
        return False
    tags = _scope_tags(issue_tags(issue))
    if any((queue, tag) in scope.park_queues for tag in tags):
        return True
    # A known different park is never overridden by a robot-number match.
    return not (tags & scope.park_tags) and queue in anchor_queues


def load_issue_scope(db: Session, user: User) -> IssueScope:
    if rbac.is_admin_or_royal(user):
        return IssueScope(frozenset(), frozenset(), frozenset(), True)
    return IssueScope(
        queues=frozenset(allowed_queues_for_user(db, user)),
        allowed_tags=frozenset(allowed_park_tags_for_user(db, user)),
        park_tags=frozenset(all_park_tags(db)),
        view_untagged=can_view_untagged(db, user),
    )


def is_issue_in_scope(
    db: Session, user: User, issue: dict, *, scope: IssueScope | None = None
) -> bool:
    """Non-raising scope check, used to filter list responses."""
    try:
        _check_issue_scope(db, user, issue, scope=scope)
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


def _check_issue_scope(
    db: Session, user: User, issue: dict, *, scope: IssueScope | None = None
) -> None:
    """Fail-closed scope check: an issue must provably belong to the user's parks.

    Anything unverifiable (missing queue, unknown tag, no park assigned) is denied
    for non-admin roles instead of being silently allowed.
    """
    if rbac.is_admin_or_royal(user):
        return

    if not is_issue_status_visible(user, issue):
        _deny()

    # 1. Queue must be present and inside the user's allowed set.
    issue_queue = str(issue.get("queue") or "").strip()
    queues = scope.queues if scope is not None else allowed_queues_for_user(db, user)
    if not issue_queue or not queues or issue_queue not in queues:
        _deny()

    # 2. Park scope by tag.
    allowed_tags = scope.allowed_tags if scope is not None else allowed_park_tags_for_user(db, user)
    tags = issue_tags(issue)
    normalized_allowed_tags = _scope_tags(allowed_tags)
    normalized_tags = _scope_tags(tags)

    if normalized_tags & normalized_allowed_tags:
        return

    # Tags belonging to a different park are always out of scope.
    park_tags = scope.park_tags if scope is not None else all_park_tags(db)
    normalized_park_tags = _scope_tags(park_tags)
    if normalized_tags & (normalized_park_tags - normalized_allowed_tags):
        _deny()

    # No park tag at all (or only non-park tags like "donor"): this is an
    # «untagged» issue, reachable only when the policy allows it.
    view_untagged = scope.view_untagged if scope is not None else can_view_untagged(db, user)
    if not view_untagged:
        _deny()


def ensure_action_allowed(db: Session, user: User, issue: dict, action: str) -> None:
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    enforce_issue_scope(db, user, issue)
    if action == "attach":
        if not rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_ATTACH):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_attach_disabled",
            )
        return
    if not can_write_tracker(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tracker_write_disabled",
        )
