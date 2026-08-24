from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import AccessStatus, User, UserRole
from robopark_api.schemas import (
    TrackerActionOut,
    TrackerAssignIn,
    TrackerCommentIn,
    TrackerTransitionIn,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_client
from robopark_api.services.tracker_policy import ensure_action_allowed

router = APIRouter(prefix="/tracker", tags=["tracker-actions"])


def _ensure_tracker_user(user: User) -> None:
    allowed_roles = {UserRole.admin.value, UserRole.royal.value, UserRole.operator.value, UserRole.mechanic.value}
    if user.role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if user.role in {UserRole.operator.value, UserRole.mechanic.value} and user.access_status != AccessStatus.approved.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _get_issue_or_404(token: str, key: str) -> dict:
    issue = tracker_client.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return issue


def _ok(key: str, action: str, user: User, issue: dict) -> TrackerActionOut:
    return TrackerActionOut(
        key=key,
        action=action,
        status=str(issue.get("status") or "updated"),
        actor=user.username,
        performed_at=datetime.now(timezone.utc).isoformat(),
    )


@router.post("/issues/{key}/comment", response_model=TrackerActionOut)
def add_comment(
    key: str,
    payload: TrackerCommentIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")
    issue = _get_issue_or_404(token, key)
    ensure_action_allowed(db, user, issue, "comment")
    try:
        tracker_client.add_comment(token=token, key=key, text=payload.text)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc
    return _ok(key, "comment", user, issue)


@router.post("/issues/{key}/assign", response_model=TrackerActionOut)
def assign_issue(
    key: str,
    payload: TrackerAssignIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")
    issue = _get_issue_or_404(token, key)
    ensure_action_allowed(db, user, issue, "assign")
    try:
        tracker_client.assign_issue(token=token, key=key, assignee=payload.assignee)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc
    return _ok(key, "assign", user, issue)


@router.post("/issues/{key}/unassign", response_model=TrackerActionOut)
def unassign_issue(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")
    issue = _get_issue_or_404(token, key)
    ensure_action_allowed(db, user, issue, "unassign")
    try:
        tracker_client.unassign_issue(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc
    return _ok(key, "unassign", user, issue)


@router.post("/issues/{key}/transition", response_model=TrackerActionOut)
def transition_issue(
    key: str,
    payload: TrackerTransitionIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")
    issue = _get_issue_or_404(token, key)
    ensure_action_allowed(db, user, issue, "transition")

    transitions = tracker_client.list_transitions(token=token, key=key)
    transition_ids = {item["id"] for item in transitions}
    if payload.transition not in transition_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tracker_transition_invalid")

    try:
        tracker_client.transition_issue(
            token=token,
            key=key,
            transition=payload.transition,
            resolution=payload.resolution,
        )
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc
    return _ok(key, "transition", user, issue)


@router.post("/issues/{key}/close", response_model=TrackerActionOut)
def close_issue(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="tracker_token_not_configured")
    issue = _get_issue_or_404(token, key)
    ensure_action_allowed(db, user, issue, "close")
    transitions = tracker_client.list_transitions(token=token, key=key)
    close_transition = next(
        (item for item in transitions if "close" in item["id"].lower() or "закры" in item["display"].lower()),
        None,
    )
    if not close_transition:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tracker_close_transition_not_found")
    try:
        tracker_client.transition_issue(token=token, key=key, transition=close_transition["id"])
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error") from exc
    return _ok(key, "close", user, issue)
