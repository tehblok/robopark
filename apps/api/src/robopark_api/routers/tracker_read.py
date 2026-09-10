from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import get_user_parks, require_user
from robopark_api.models import Park, User
from robopark_api.routers._blockers import blocker_out as _blocker_out
from robopark_api.schemas import (
    RobotTicketsOut,
    TrackerAttachmentOut,
    TrackerCommentOut,
    TrackerIssueCapabilitiesOut,
    TrackerIssueDetailOut,
    TrackerIssueOut,
    TrackerIssuesOut,
    TrackerPersonOut,
    TrackerTransitionOut,
    TrackerUserOut,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import rbac, tracker_cache, tracker_client, tracker_filters
from robopark_api.services import tracker_signatures as sig_svc
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.tracker_assignees import list_assignee_candidates
from robopark_api.services.tracker_claims import (
    local_assignee,
    local_assignees,
    mechanic_can_access_issue,
)
from robopark_api.services.tracker_policy import (
    allowed_park_tags_for_user,
    allowed_queues_for_user,
    can_view_untagged,
    can_write_tracker,
    enforce_issue_scope,
    is_issue_in_scope,
    load_issue_scope,
)

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tracker", tags=["tracker-read"])


def _ensure_tracker_user(user: User, db: Session) -> None:
    rbac.assert_approved_or_staff(user)
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ):
        return
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_WRITE):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _enforce_mechanic_claim(db: Session, user: User, issue: dict) -> None:
    if not mechanic_can_access_issue(db, user, issue):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="tracker_issue_claim_required",
        )


def _person_out(raw: object) -> TrackerPersonOut | None:
    if not isinstance(raw, dict):
        return None
    display = str(raw.get("display") or "").strip()
    login = str(raw.get("login") or "").strip()
    if not display and not login:
        return None
    return TrackerPersonOut(display=display or login, login=login)


def _issue_out(
    issue: dict,
    *,
    db: Session | None = None,
    assignee_override: dict[str, str] | None = None,
) -> TrackerIssueOut:
    assignee = assignee_override
    if assignee is None and db is not None:
        assignee = local_assignee(db, issue)
    elif assignee is None and db is None:
        assignee = issue.get("assignee")
    return TrackerIssueOut(
        key=str(issue.get("key") or ""),
        summary=str(issue.get("summary") or ""),
        status=str(issue.get("status") or ""),
        status_key=str(issue.get("status_key") or ""),
        queue=str(issue.get("queue") or ""),
        robot=issue.get("robot"),
        created_at=issue.get("created"),
        updated_at=issue.get("updated"),
        hours_created=issue.get("hours_created"),
        url=tracker_client.build_issue_url(str(issue.get("key") or "")),
        tags=[str(tag) for tag in (issue.get("tags") or [])],
        priority=str(issue.get("priority") or ""),
        type=str(issue.get("type") or ""),
        assignee=_person_out(assignee),
    )


def _normalized_robot_number(raw: object) -> str | None:
    text = str(raw or "").strip().upper()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text[6:] if text.startswith("YASADR") else text.removeprefix("A")
    if not text or not text.isdecimal():
        return None
    return text.lstrip("0") or "0"


def _detail_out(issue: dict, *, db: Session, user: User) -> TrackerIssueDetailOut:
    attachments = [TrackerAttachmentOut(**item) for item in (issue.get("attachments") or [])]
    writable = can_write_tracker(db, user)
    return TrackerIssueDetailOut(
        **_issue_out(issue, db=db).model_dump(),
        resolution=str(issue.get("resolution") or ""),
        description=str(issue.get("description") or ""),
        reporter=_person_out(issue.get("reporter")),
        components=[str(item) for item in (issue.get("components") or [])],
        attachments=attachments,
        capabilities=TrackerIssueCapabilitiesOut(
            comment=writable,
            assign=writable,
            unassign=writable,
            transition=writable,
            close=writable,
            attach=rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_ATTACH),
        ),
    )


def _untagged_park_tags(db: Session, user: User) -> list[str]:
    """Tags to exclude for «неразмеченные»: user's parks, or all active parks for admin."""
    tags = sorted(t for t in allowed_park_tags_for_user(db, user) if t)
    if tags:
        return tags
    if user.role in {RoleSlug.ADMIN, RoleSlug.ROYAL}:
        rows = db.scalars(select(Park).where(Park.is_active.is_(True))).all()
        return sorted(
            {str(park.tag).strip() for park in rows if park.tag and str(park.tag).strip()}
        )
    return []


def _build_query(
    *,
    user: User,
    db: Session,
    queue: str | None,
    park: str | None,
    status_filter: str | None,
    robot: str | None,
    assignee: str | None,
    untagged: bool,
    related_repairs: bool = False,
) -> str:
    parts: list[str] = [] if related_repairs else ["Priority: blocker"]
    # Explicit Status replaces the default open-issues clause (avoid conflicting QL).
    if status_filter:
        bucket = tracker_filters.status_bucket(status_filter)
        if user.role == RoleSlug.DRIVER and bucket not in {"new", "moving"}:
            raise HTTPException(status_code=403, detail="tracker_status_forbidden")
        aliases = tracker_filters.STATUS_BUCKETS.get(status_filter)
        if aliases:
            parts.append(
                "("
                + " OR ".join(f"Status: {tracker_client.ql_token(alias)}" for alias in aliases)
                + ")"
            )
        else:
            parts.append(f"Status: {tracker_client.ql_token(status_filter)}")
    else:
        parts.append(tracker_client.open_issues_clause())
    if user.role == RoleSlug.DRIVER:
        aliases = (
            *tracker_filters.STATUS_BUCKETS["new"],
            *tracker_filters.STATUS_BUCKETS["moving"],
        )
        parts.append(
            "("
            + " OR ".join(f"Status: {tracker_client.ql_token(alias)}" for alias in aliases)
            + ")"
        )

    queues = allowed_queues_for_user(db, user)
    selected_queue = (queue or "").strip() or None
    if user.role not in {RoleSlug.ADMIN, RoleSlug.ROYAL}:
        if not queues:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_scope_empty",
            )
        if selected_queue and selected_queue not in queues:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_queue_forbidden",
            )
        if selected_queue:
            parts.append(f"Queue: {tracker_client.ql_token(selected_queue)}")
        elif queues:
            if len(queues) == 1:
                selected_queue = queues[0]
                parts.append(f"Queue: {tracker_client.ql_token(selected_queue)}")
            else:
                parts.append(
                    "(" + " OR ".join(f"Queue: {tracker_client.ql_token(q)}" for q in queues) + ")"
                )
    elif selected_queue:
        parts.append(f"Queue: {tracker_client.ql_token(selected_queue)}")
    else:
        # Never run an unscoped search — default to fleet ops queue.
        selected_queue = tracker_client.DEFAULT_QUEUE
        parts.append(f"Queue: {tracker_client.ql_token(selected_queue)}")

    # Related repairs override fleet defaults, which can include non-repair types.
    if related_repairs:
        parts.append("Type: repair")
    elif selected_queue:
        type_part = tracker_client.type_clause(selected_queue)
        if type_part:
            parts.append(type_part)

    if robot:
        parts.append(f"Summary: {tracker_client.ql_quote(robot)}")

    assignee_part = tracker_client.assignee_clause(assignee or "")
    if assignee_part:
        parts.append(assignee_part)

    if park:
        allowed_tags = allowed_park_tags_for_user(db, user)
        if user.role not in {RoleSlug.ADMIN, RoleSlug.ROYAL} and park not in allowed_tags:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_park_forbidden",
            )
        parts.append(f"Tags: {tracker_client.ql_token(park)}")
    elif untagged:
        if not can_view_untagged(db, user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_untagged_forbidden",
            )
        if not selected_queue:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tracker_queue_required_for_untagged",
            )
        # Retain explicit status/robot/assignee filters while excluding park tags.
        parts.extend(tracker_client.exclude_tag(tag) for tag in _untagged_park_tags(db, user))

    return tracker_client.join_query(*parts)


@router.get("/issues", response_model=TrackerIssuesOut)
def list_issues(
    queue: str | None = Query(default=None),
    park: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    open_only: bool = Query(default=False),
    robot: str | None = Query(default=None),
    robot_exact: str | None = Query(
        default=None, max_length=tracker_client.MAX_ROBOT_REFERENCE_LENGTH
    ),
    related_repairs: bool = Query(default=False),
    exclude_key: str | None = Query(default=None, max_length=128),
    assignee: str | None = Query(default=None, max_length=128),
    untagged: bool = Query(default=False),
    age_hours: int | None = Query(default=None, ge=1),
    sort_order: Literal["oldest", "newest"] = Query(default="oldest", alias="sort"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerIssuesOut:
    _ensure_tracker_user(user, db)
    exact_robot = _normalized_robot_number(robot_exact)
    if related_repairs and exact_robot is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracker_robot_exact_required",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    query_text = _build_query(
        user=user,
        db=db,
        queue=queue,
        park=park,
        status_filter=status_filter,
        robot=robot,
        assignee=assignee,
        untagged=untagged,
        related_repairs=related_repairs,
    )
    if open_only and status_filter:
        query_text = tracker_client.join_query(query_text, tracker_client.open_issues_clause())
    if robot_exact is not None:
        if exact_robot is None:
            return TrackerIssuesOut(items=[], total=0, limit=limit, offset=offset, has_more=False)
        query_text = tracker_client.join_query(
            query_text, tracker_client.robot_summary_clause(exact_robot)
        )
    resolved_until = datetime.now(UTC)
    resolved_since = (
        resolved_until - timedelta(days=14)
        if related_repairs and (status_filter or "").strip().lower() == "closed"
        else None
    )
    if resolved_since is not None:
        # Keep the search cache reusable between pages. The exact rolling
        # boundary is applied below, including when a cached row ages out.
        query_since = resolved_since.replace(minute=0, second=0, microsecond=0)
        query_text = tracker_client.join_query(
            query_text, f'Resolved: >= "{query_since:%Y-%m-%d %H:%M:%S}"'
        )
    try:
        items = tracker_cache.search_issues(
            token=token, query=query_text, filter_open=open_only or not bool(status_filter)
        )
    except tracker_client.TrackerError as exc:
        logger.exception("tracker search failed query=%r", query_text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc

    # Give the existing age/date ordering a stable final key independent of
    # whichever order the upstream service happened to return equal records.
    keyed_items = sorted(items, key=lambda issue: str(issue.get("key") or ""))
    ordered = tracker_filters.sort_issues_oldest_first(keyed_items)
    if sort_order == "newest":
        ordered.reverse()

    excluded_key = (exclude_key or "").strip()
    scoped_raw: list[dict] = []
    seen_keys: set[str] = set()
    # Raw upstream data is shared; authorization is loaded afresh for this
    # response after the upstream wait and reused only across its rows.
    scope = load_issue_scope(db, user)
    for issue in ordered:
        # Out-of-scope issues are filtered out, not fatal: a single foreign issue
        # in the upstream response must not fail the whole listing.
        if not is_issue_in_scope(db, user, issue, scope=scope):
            continue
        if related_repairs and str(issue.get("type_key") or "").strip() != "repair":
            continue
        if resolved_since is not None:
            try:
                resolved = datetime.fromisoformat(str(issue.get("resolved") or ""))
            except ValueError:
                continue
            if resolved.tzinfo is None:
                resolved = resolved.replace(tzinfo=UTC)
            if not resolved_since <= resolved <= resolved_until:
                continue
        if robot_exact is not None and (
            exact_robot is None or _normalized_robot_number(issue.get("robot")) != exact_robot
        ):
            continue
        key = str(issue.get("key") or "").strip()
        if key == excluded_key or key in seen_keys:
            continue
        if age_hours and issue.get("hours_created"):
            try:
                if float(issue["hours_created"]) < age_hours:
                    continue
            except (TypeError, ValueError):
                pass
        seen_keys.add(key)
        scoped_raw.append(issue)

    total = len(scoped_raw)
    page_raw = scoped_raw[offset : offset + limit]
    assignments = local_assignees(db, [str(issue.get("key") or "") for issue in page_raw])
    page = [
        _issue_out(
            {
                **issue,
                "assignee": assignments.get(str(issue.get("key") or "")),
            }
        )
        for issue in page_raw
    ]
    return TrackerIssuesOut(
        items=page,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(page) < total,
    )


@router.get("/users", response_model=list[TrackerUserOut])
def search_tracker_users(
    q: str = Query(min_length=1, max_length=64),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TrackerUserOut]:
    _ensure_tracker_user(user, db)
    return [TrackerUserOut(**item) for item in list_assignee_candidates(db, user, q)]


@router.get("/issues/{key}", response_model=TrackerIssueDetailOut)
def get_issue(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerIssueDetailOut:
    _ensure_tracker_user(user, db)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )
    try:
        issue = tracker_cache.get_issue(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)
    _enforce_mechanic_claim(db, user, issue)
    return _detail_out(issue, db=db, user=user)


@router.get("/issues/{key}/comments", response_model=list[TrackerCommentOut])
def get_comments(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TrackerCommentOut]:
    _ensure_tracker_user(user, db)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    issue = tracker_cache.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)
    _enforce_mechanic_claim(db, user, issue)

    try:
        comments = tracker_cache.list_comments(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc
    if user.role == RoleSlug.MECHANIC:
        comments = sig_svc.filter_mechanic_visible_comments(db, comments)
    return [
        TrackerCommentOut(
            **{
                **item,
                "attachments": [
                    TrackerAttachmentOut(**attachment)
                    for attachment in (item.get("attachments") or [])
                ],
            }
        )
        for item in comments
    ]


@router.get("/transitions/{key}", response_model=list[TrackerTransitionOut])
def get_transitions(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TrackerTransitionOut]:
    _ensure_tracker_user(user, db)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    issue = tracker_cache.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)
    _enforce_mechanic_claim(db, user, issue)

    try:
        transitions = tracker_cache.list_transitions(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc
    return [TrackerTransitionOut(**item) for item in transitions]


def _robot_search_queues(db: Session, user: User) -> list[str]:
    scoped = allowed_queues_for_user(db, user)
    if scoped:
        return scoped
    parks = (
        list(db.scalars(select(Park).where(Park.is_active.is_(True))).all())
        if rbac.is_admin_or_royal(user)
        else get_user_parks(db, user)
    )
    seen: set[str] = set()
    queues: list[str] = []
    for park in parks:
        queue = (park.tracker_queue or "").strip()
        if queue and queue not in seen:
            seen.add(queue)
            queues.append(queue)
    if queues:
        return queues
    if rbac.is_admin_or_royal(user):
        return [tracker_client.DEFAULT_QUEUE]
    return []


@router.get("/robots/{query}/tickets", response_model=RobotTicketsOut)
def robot_tickets(
    query: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> RobotTicketsOut:
    _ensure_tracker_user(user, db)
    queues = _robot_search_queues(db, user)
    if not queues:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no_tracker_parks",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    allowed = set(queues)
    merged: list[dict] = []
    keys: set[str] = set()
    try:
        for queue in queues:
            for item in tracker_cache.search_robot_tickets(token=token, queue=queue, query=query):
                item_queue = (item.get("queue") or "").strip()
                if item_queue and item_queue not in allowed:
                    continue
                if item["key"] in keys:
                    continue
                keys.add(item["key"])
                merged.append(item)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc

    scope = load_issue_scope(db, user)
    sorted_items = tracker_filters.sort_issues_oldest_first(
        [item for item in merged if is_issue_in_scope(db, user, item, scope=scope)]
    )
    return RobotTicketsOut(
        query=query,
        items=[_blocker_out(item) for item in sorted_items],
    )
