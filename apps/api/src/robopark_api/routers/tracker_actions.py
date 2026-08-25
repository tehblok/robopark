from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import get_mechanic_park, require_user
from robopark_api.models import AccessStatus, User, UserRole
from robopark_api.schemas import (
    TrackerActionOut,
    TrackerAssignIn,
    TrackerCommentIn,
    TrackerTransitionIn,
)
from robopark_api.services import audit, tracker_client
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import reports as reports_svc
from robopark_api.services.login_throttle import client_ip
from robopark_api.services.tracker_policy import ensure_action_allowed

router = APIRouter(prefix="/tracker", tags=["tracker-actions"])


def _ensure_tracker_user(user: User) -> None:
    allowed_roles = {
        UserRole.admin.value,
        UserRole.royal.value,
        UserRole.operator.value,
        UserRole.mechanic.value,
    }
    if user.role not in allowed_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if (
        user.role in {UserRole.operator.value, UserRole.mechanic.value}
        and user.access_status != AccessStatus.approved.value
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _require_token(db: Session) -> str:
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )
    return token


def _get_issue_or_404(token: str, key: str) -> dict:
    issue = tracker_client.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return issue


def _authorize(db: Session, user: User, issue: dict, action: str, request: Request) -> None:
    """Check the policy and audit a denial before propagating it."""
    try:
        ensure_action_allowed(db, user, issue, action)
    except HTTPException as exc:
        audit.record(
            db,
            action=audit.TRACKER_ACTIONS.get(action, action),
            actor=user,
            target_type="tracker_issue",
            target_id=str(issue.get("key") or ""),
            outcome=audit.OUTCOME_DENIED,
            detail=audit.describe(exc.detail),
            client_ip=client_ip(request),
        )
        raise


def _upstream_error(
    db: Session, user: User, action: str, key: str, exc: Exception, request: Request
) -> HTTPException:
    audit.record(
        db,
        action=audit.TRACKER_ACTIONS.get(action, action),
        actor=user,
        target_type="tracker_issue",
        target_id=key,
        outcome=audit.OUTCOME_FAILURE,
        detail=audit.describe(exc),
        client_ip=client_ip(request),
    )
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="tracker_upstream_error")


def _ok(key: str, action: str, user: User, issue: dict) -> TrackerActionOut:
    return TrackerActionOut(
        key=key,
        action=action,
        status=str(issue.get("status") or "updated"),
        actor=user.username,
        performed_at=datetime.now(UTC).isoformat(),
    )


@router.post("/issues/{key}/comment", response_model=TrackerActionOut)
def add_comment(
    key: str,
    payload: TrackerCommentIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "comment", request)

    # Tracker sees a single service account, so the real author is signed
    # into the comment body.
    text = f"{payload.text}{audit.signature_for(user)}"
    try:
        tracker_client.add_comment(token=token, key=key, text=text)
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "comment", key, exc, request) from exc

    audit.record(
        db,
        action=audit.ACTION_TRACKER_COMMENT,
        actor=user,
        target_type="tracker_issue",
        target_id=key,
        detail=audit.describe(payload.text, limit=200),
        client_ip=client_ip(request),
    )
    return _ok(key, "comment", user, issue)


@router.post("/issues/{key}/assign", response_model=TrackerActionOut)
def assign_issue(
    key: str,
    payload: TrackerAssignIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "assign", request)

    try:
        tracker_client.assign_issue(token=token, key=key, assignee=payload.assignee)
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "assign", key, exc, request) from exc

    audit.record(
        db,
        action=audit.ACTION_TRACKER_ASSIGN,
        actor=user,
        target_type="tracker_issue",
        target_id=key,
        detail=f"assignee={payload.assignee}",
        client_ip=client_ip(request),
    )
    return _ok(key, "assign", user, issue)


@router.post("/issues/{key}/unassign", response_model=TrackerActionOut)
def unassign_issue(
    key: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "unassign", request)

    try:
        tracker_client.unassign_issue(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "unassign", key, exc, request) from exc

    audit.record(
        db,
        action=audit.ACTION_TRACKER_UNASSIGN,
        actor=user,
        target_type="tracker_issue",
        target_id=key,
        client_ip=client_ip(request),
    )
    return _ok(key, "unassign", user, issue)


@router.post("/issues/{key}/transition", response_model=TrackerActionOut)
def transition_issue(
    key: str,
    payload: TrackerTransitionIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "transition", request)

    transitions = tracker_client.list_transitions(token=token, key=key)
    transition_ids = {item["id"] for item in transitions}
    if payload.transition not in transition_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracker_transition_invalid",
        )

    try:
        tracker_client.transition_issue(
            token=token,
            key=key,
            transition=payload.transition,
            resolution=payload.resolution,
        )
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "transition", key, exc, request) from exc

    audit.record(
        db,
        action=audit.ACTION_TRACKER_TRANSITION,
        actor=user,
        target_type="tracker_issue",
        target_id=key,
        detail=f"transition={payload.transition} resolution={payload.resolution or '-'}",
        client_ip=client_ip(request),
    )
    return _ok(key, "transition", user, issue)


@router.post("/issues/{key}/close", response_model=TrackerActionOut)
def close_issue(
    key: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user)
    token = _require_token(db)

    mechanic_park = None
    if user.role == UserRole.mechanic.value:
        mechanic_park = get_mechanic_park(db, user)
        if mechanic_park is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mechanic_park_required_for_close_review",
            )

    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "close", request)

    transitions = tracker_client.list_transitions(token=token, key=key)
    close_transition = next(
        (
            item
            for item in transitions
            if "close" in item["id"].lower() or "закры" in item["display"].lower()
        ),
        None,
    )
    if not close_transition:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracker_close_transition_not_found",
        )

    try:
        tracker_client.transition_issue(token=token, key=key, transition=close_transition["id"])
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "close", key, exc, request) from exc

    audit.record(
        db,
        action=audit.ACTION_TRACKER_CLOSE,
        actor=user,
        park_id=mechanic_park.id if mechanic_park else None,
        target_type="tracker_issue",
        target_id=key,
        client_ip=client_ip(request),
    )

    if mechanic_park is not None:
        try:
            reports_svc.get_or_create_close_review(
                db,
                author=user,
                park_id=mechanic_park.id,
                tracker_key=key,
                tracker_url=tracker_client.build_issue_url(key),
                title=f"Закрытие {key}",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="tracker_closed_report_failed",
            ) from exc

    return _ok(key, "close", user, issue)
