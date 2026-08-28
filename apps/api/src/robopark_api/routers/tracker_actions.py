from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import get_user_parks, require_user
from robopark_api.models import Park, User
from robopark_api.services import rbac
from robopark_api.services.rbac import RoleSlug
from robopark_api.schemas import (
    TrackerActionOut,
    TrackerAssignIn,
    TrackerCommentIn,
    TrackerTransitionIn,
)
from robopark_api.services import audit, tracker_cache, tracker_client
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import reports as reports_svc
from robopark_api.services import tracker_signatures as sig_svc
from robopark_api.services.login_throttle import client_ip
from robopark_api.services.tracker_policy import ensure_action_allowed, issue_tags

router = APIRouter(prefix="/tracker", tags=["tracker-actions"])

PHOTO_COMMENT_BODY = "Фото неисправности"
_ATTACHMENT_FILENAME_RE = re.compile(r"[^\w.\-() ]+", re.UNICODE)


def _sanitize_attachment_filename(name: str | None) -> str:
    raw = (name or "photo.jpg").strip()
    base = raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = _ATTACHMENT_FILENAME_RE.sub("_", base).strip("._")
    return cleaned or "photo.jpg"


def _ensure_tracker_user(user: User, db: Session) -> None:
    rbac.assert_approved_or_staff(user)
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ):
        return
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_WRITE):
        return
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_ATTACH):
        return
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
    issue = tracker_cache.get_issue(token=token, key=key)
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


def _signed_tracker_text(
    db: Session,
    user: User,
    issue: dict,
    body: str,
) -> str:
    ctx = sig_svc.build_signature_context(db, user, issue)
    return sig_svc.format_signed_comment(
        body=body,
        park_name=ctx.park_name,
        mechanic_login=ctx.mechanic_login,
        operator_login=ctx.operator_login,
    )


@router.post("/issues/{key}/comment", response_model=TrackerActionOut)
def add_comment(
    key: str,
    payload: TrackerCommentIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user, db)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "comment", request)

    body = payload.text.strip()
    signed_text = _signed_tracker_text(db, user, issue, body)
    try:
        tracker_client.add_comment(token=token, key=key, text=signed_text)
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "comment", key, exc, request) from exc
    tracker_cache.invalidate_issue(key)

    park = sig_svc.resolve_park(db, issue)
    audit.record(
        db,
        action=audit.ACTION_TRACKER_COMMENT,
        actor=user,
        park_id=park.id if park is not None else None,
        target_type="tracker_issue",
        target_id=key,
        detail=audit.describe(body, limit=200),
        client_ip=client_ip(request),
    )
    return _ok(key, "comment", user, issue)


@router.post("/issues/{key}/attachments", response_model=TrackerActionOut)
async def attach_file(
    key: str,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user, db)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "attach", request)

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracker_attachment_empty",
        )
    if len(content) > tracker_client.MAX_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracker_attachment_too_large",
        )

    filename = _sanitize_attachment_filename(file.filename)
    content_type = tracker_client.normalize_attachment_content_type(
        filename=filename,
        content=content,
        content_type=file.content_type,
    )
    if not content_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracker_attachment_invalid_type",
        )
    try:
        temp_id = tracker_client.upload_temp_attachment(
            token=token,
            filename=filename,
            content=content,
            content_type=content_type,
        )
        signed_text = _signed_tracker_text(db, user, issue, PHOTO_COMMENT_BODY)
        tracker_client.add_comment(
            token=token,
            key=key,
            text=signed_text,
            attachment_ids=[temp_id],
        )
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "attach", key, exc, request) from exc
    tracker_cache.invalidate_issue(key)

    park = sig_svc.resolve_park(db, issue)
    audit.record(
        db,
        action=audit.ACTION_TRACKER_ATTACH,
        actor=user,
        park_id=park.id if park is not None else None,
        target_type="tracker_issue",
        target_id=key,
        detail=audit.describe(
            f"name={filename} size={len(content)}",
            limit=200,
        ),
        client_ip=client_ip(request),
    )
    return _ok(key, "attach", user, issue)


@router.post("/issues/{key}/assign", response_model=TrackerActionOut)
def assign_issue(
    key: str,
    payload: TrackerAssignIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _ensure_tracker_user(user, db)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "assign", request)

    try:
        tracker_client.assign_issue(token=token, key=key, assignee=payload.assignee)
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "assign", key, exc, request) from exc
    tracker_cache.invalidate_issue(key)

    park = sig_svc.resolve_park(db, issue)
    audit.record(
        db,
        action=audit.ACTION_TRACKER_ASSIGN,
        actor=user,
        park_id=park.id if park is not None else None,
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
    _ensure_tracker_user(user, db)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "unassign", request)

    try:
        tracker_client.unassign_issue(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise _upstream_error(db, user, "unassign", key, exc, request) from exc
    tracker_cache.invalidate_issue(key)

    park = sig_svc.resolve_park(db, issue)
    audit.record(
        db,
        action=audit.ACTION_TRACKER_UNASSIGN,
        actor=user,
        park_id=park.id if park is not None else None,
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
    _ensure_tracker_user(user, db)
    token = _require_token(db)
    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "transition", request)

    transitions = tracker_cache.list_transitions(token=token, key=key)
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
    tracker_cache.invalidate_issue(key)

    park = sig_svc.resolve_park(db, issue)
    audit.record(
        db,
        action=audit.ACTION_TRACKER_TRANSITION,
        actor=user,
        park_id=park.id if park is not None else None,
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
    _ensure_tracker_user(user, db)
    token = _require_token(db)

    parks: list[Park] = []
    mechanic_park = None
    if user.role == RoleSlug.MECHANIC:
        parks = get_user_parks(db, user)
        if not parks:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mechanic_park_required_for_close_review",
            )

    issue = _get_issue_or_404(token, key)
    _authorize(db, user, issue, "close", request)

    if user.role == RoleSlug.MECHANIC:
        tags = issue_tags(issue)
        matched = [
            park
            for park in parks
            if park.tag and str(park.tag).strip() in tags
        ]
        mechanic_park = matched[0] if matched else parks[0]

    transitions = tracker_cache.list_transitions(token=token, key=key)
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
    tracker_cache.invalidate_issue(key)

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
