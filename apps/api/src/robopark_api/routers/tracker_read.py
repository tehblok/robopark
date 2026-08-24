from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import AccessStatus, User, UserRole
from robopark_api.schemas import (
    TrackerCommentOut,
    TrackerIssueDetailOut,
    TrackerIssueOut,
    TrackerIssuesOut,
    TrackerTransitionOut,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_client
from robopark_api.services.tracker_policy import (
    allowed_park_tags_for_user,
    allowed_queues_for_user,
    can_view_untagged,
    enforce_issue_scope,
)

router = APIRouter(prefix="/tracker", tags=["tracker-read"])


def _ensure_tracker_user(user: User) -> None:
    allowed_roles = {UserRole.admin.value, UserRole.royal.value, UserRole.operator.value, UserRole.mechanic.value}
    if user.role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if user.role in {UserRole.operator.value, UserRole.mechanic.value} and user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _issue_out(issue: dict) -> TrackerIssueOut:
    return TrackerIssueOut(
        key=str(issue.get("key") or ""),
        summary=str(issue.get("summary") or ""),
        status=str(issue.get("status") or ""),
        status_key=str(issue.get("status_key") or ""),
        queue=str(issue.get("queue") or ""),
        robot=issue.get("robot"),
        created_at=issue.get("created"),
        hours_created=issue.get("hours_created"),
        url=tracker_client.build_issue_url(str(issue.get("key") or "")),
    )


def _detail_out(issue: dict) -> TrackerIssueDetailOut:
    return TrackerIssueDetailOut(**_issue_out(issue).model_dump(), resolution=str(issue.get("resolution") or ""))


def _build_query(
    *,
    user: User,
    db: Session,
    queue: str | None,
    park: str | None,
    status_filter: str | None,
    robot: str | None,
    untagged: bool,
) -> str:
    parts: list[str] = ["Priority: blocker", "Resolution: empty()"]

    if status_filter:
        parts.append(f"Status: {tracker_client.ql_quote(status_filter)}")

    queues = allowed_queues_for_user(db, user)
    if user.role not in {UserRole.admin.value, UserRole.royal.value}:
        if queue and queue not in queues:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tracker_queue_forbidden")
        if queue:
            parts.append(f"Queue: {queue}")
        elif queues:
            parts.append("(" + " OR ".join(f"Queue: {q}" for q in queues) + ")")
    elif queue:
        parts.append(f"Queue: {queue}")

    if robot:
        parts.append(f"Summary: {tracker_client.ql_quote(robot)}")

    if park:
        allowed_tags = allowed_park_tags_for_user(db, user)
        if user.role not in {UserRole.admin.value, UserRole.royal.value} and park not in allowed_tags:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tracker_park_forbidden")
        parts.append(f"Tags: {tracker_client.ql_quote(park)}")
    elif untagged:
        if not can_view_untagged(db, user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="tracker_untagged_forbidden")
        parts.append("Tags: empty()")

    return tracker_client.join_query(*parts)


@router.get("/issues", response_model=TrackerIssuesOut)
def list_issues(
    queue: str | None = Query(default=None),
    park: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    robot: str | None = Query(default=None),
    untagged: bool = Query(default=False),
    age_hours: int | None = Query(default=None, ge=1),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerIssuesOut:
    _ensure_tracker_user(user)

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")

    query_text = _build_query(
        user=user,
        db=db,
        queue=queue,
        park=park,
        status_filter=status_filter,
        robot=robot,
        untagged=untagged,
    )
    try:
        items = tracker_client.search_issues(token=token, query=query_text)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc

    scoped: list[TrackerIssueOut] = []
    for issue in items:
        enforce_issue_scope(db, user, issue)
        if age_hours and issue.get("hours_created"):
            try:
                if float(issue["hours_created"]) < age_hours:
                    continue
            except (TypeError, ValueError):
                pass
        scoped.append(_issue_out(issue))
    return TrackerIssuesOut(items=scoped[:200])


@router.get("/issues/{key}", response_model=TrackerIssueDetailOut)
def get_issue(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerIssueDetailOut:
    _ensure_tracker_user(user)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")
    try:
        issue = tracker_client.get_issue(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)
    return _detail_out(issue)


@router.get("/issues/{key}/comments", response_model=list[TrackerCommentOut])
def get_comments(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TrackerCommentOut]:
    _ensure_tracker_user(user)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")

    issue = tracker_client.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)

    try:
        comments = tracker_client.list_comments(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc
    return [TrackerCommentOut(**item) for item in comments]


@router.get("/transitions/{key}", response_model=list[TrackerTransitionOut])
def get_transitions(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TrackerTransitionOut]:
    _ensure_tracker_user(user)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")

    issue = tracker_client.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)

    try:
        transitions = tracker_client.list_transitions(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc
    return [TrackerTransitionOut(**item) for item in transitions]
