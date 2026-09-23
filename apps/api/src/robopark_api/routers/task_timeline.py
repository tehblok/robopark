"""Unified task chat, attachments, and defect-code endpoints."""

from datetime import UTC, datetime
from functools import partial
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.routers.tracker_actions import _authorize
from robopark_api.schemas import (
    DefectCodeOut,
    TaskAttachmentStagedOut,
    TaskTimelineItemOut,
    TrackerCommentIn,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import (
    rbac,
    task_lifecycle,
    tracker_cache,
    tracker_client,
    tracker_signatures,
)
from robopark_api.services.defect_codes import DEFECT_CODES
from robopark_api.services.task_timeline import (
    append_user_message,
    attachment_content,
    merge_timeline,
    stage_attachment,
)
from robopark_api.services.tracker_claims import mechanic_can_access_issue
from robopark_api.services.tracker_policy import enforce_issue_scope
from robopark_api.services.tracker_signatures import filter_mechanic_visible_comments
from robopark_api.task_workflow_models import TaskMessage

router = APIRouter(prefix="/tracker", tags=["task-timeline"])


def _issue(db: Session, user: User, key: str, action: str | None, request: Request) -> dict:
    rbac.assert_approved_or_staff(user)
    if task_lifecycle.is_hidden(db, key):
        raise HTTPException(status_code=404)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(status_code=503, detail="tracker_token_not_configured")
    try:
        issue = tracker_cache.get_issue(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=502, detail="tracker_upstream_error") from exc
    if issue is None:
        raise HTTPException(status_code=404)
    if action is not None:
        _authorize(db, user, issue, action, request)
    else:
        if not rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ):
            raise HTTPException(status_code=403)
        enforce_issue_scope(db, user, issue)
        if rbac.role_slug(user) == rbac.RoleSlug.MECHANIC and not mechanic_can_access_issue(
            db, user, issue
        ):
            raise HTTPException(status_code=409, detail="tracker_issue_claim_required")
    return issue


@router.get("/defect-codes", response_model=list[DefectCodeOut])
def get_defect_codes(user: User = Depends(require_user)) -> list[DefectCodeOut]:
    rbac.assert_approved_or_staff(user)
    return [
        DefectCodeOut(code=item.code, label=item.label, description=item.description)
        for item in DEFECT_CODES
    ]


@router.get("/issues/{key}/timeline", response_model=list[TaskTimelineItemOut])
def get_timeline(
    key: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TaskTimelineItemOut]:
    _issue(db, user, key, None, request)
    token = settings_svc.get_tracker_token(db)
    try:
        comments = tracker_cache.list_comments(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(status_code=502, detail="tracker_upstream_error") from exc
    visibility_filter = None
    if rbac.role_slug(user) == rbac.RoleSlug.MECHANIC:
        visibility_filter = partial(filter_mechanic_visible_comments, db)
    return [
        TaskTimelineItemOut(**item)
        for item in merge_timeline(
            db,
            issue_key=key,
            comments=comments,
            tracker_visibility_filter=visibility_filter,
            include_staff_messages=rbac.role_slug(user) != rbac.RoleSlug.MECHANIC,
        )
    ]


@router.get("/issues/{key}/attachments/{attachment_id}/content")
def get_attachment_content(
    key: str,
    attachment_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    _issue(db, user, key, None, request)
    try:
        path, media_type, filename = attachment_content(
            db, issue_key=key, attachment_id=attachment_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="task_attachment_not_found") from exc
    disposition = f"inline; filename*=UTF-8''{quote(filename)}"
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.head(
    "/issues/{key}/attachments/{attachment_id}/content",
    status_code=status.HTTP_204_NO_CONTENT,
)
def authorize_attachment_content(
    key: str,
    attachment_id: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> Response:
    issue = _issue(db, user, key, None, request)
    if task_lifecycle.tracker_issue_is_closed(issue):
        raise HTTPException(status_code=409, detail="task_already_closed")
    issue_park = tracker_signatures.resolve_park(db, issue)
    if issue_park is not None and not issue_park.is_active:
        raise HTTPException(status_code=403, detail="task_park_inactive")
    try:
        attachment_content(db, issue_key=key, attachment_id=attachment_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="task_attachment_not_found") from exc
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"Cache-Control": "private, no-store"},
    )


@router.post(
    "/issues/{key}/messages",
    response_model=TaskTimelineItemOut,
    status_code=status.HTTP_201_CREATED,
)
def post_message(
    key: str,
    body: TrackerCommentIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TaskTimelineItemOut:
    _issue(db, user, key, "comment", request)
    row = append_user_message(
        db,
        issue_key=key,
        actor=user,
        text=body.text,
        idempotency_key=idempotency_key,
    )
    return TaskTimelineItemOut(
        id=row.id,
        kind=row.kind,
        author=row.author_name,
        text=row.text,
        created_at=datetime.fromtimestamp(row.created_at, UTC).isoformat(),
        sync_state=row.sync_state,
        attachments=[],
    )


@router.post(
    "/issues/{key}/photos",
    response_model=TaskAttachmentStagedOut,
    status_code=status.HTTP_201_CREATED,
)
def post_photo(
    key: str,
    request: Request,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TaskAttachmentStagedOut:
    _issue(db, user, key, "attach", request)
    content = file.file.read(tracker_client.MAX_ATTACHMENT_BYTES + 1)
    try:
        attachment, action = stage_attachment(
            db,
            actor=user,
            issue_key=key,
            message=None,
            idempotency_key=idempotency_key,
            filename=file.filename,
            content=content,
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskAttachmentStagedOut(
        id=attachment.id,
        message_id=attachment.message_id,
        name=attachment.original_name,
        mimetype=attachment.mime_type,
        size=attachment.size_bytes,
        sha256=attachment.sha256,
        action_id=action.id,
        sync_state="synced"
        if action.state == "succeeded"
        else "needs_attention"
        if action.state == "needs_attention"
        else "pending",
    )


@router.post(
    "/issues/{key}/message-attachments",
    response_model=TaskAttachmentStagedOut,
    status_code=status.HTTP_201_CREATED,
)
def post_message_attachment(
    key: str,
    request: Request,
    message_id: str = Form(...),
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TaskAttachmentStagedOut:
    _issue(db, user, key, "attach", request)
    message = db.get(TaskMessage, message_id)
    if message is None or message.issue_key != key:
        raise HTTPException(status_code=404, detail="task_message_not_found")
    content = file.file.read(tracker_client.MAX_ATTACHMENT_BYTES + 1)
    try:
        attachment, action = stage_attachment(
            db,
            actor=user,
            issue_key=key,
            message=message,
            idempotency_key=idempotency_key,
            filename=file.filename,
            content=content,
            content_type=file.content_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TaskAttachmentStagedOut(
        id=attachment.id,
        message_id=attachment.message_id,
        name=attachment.original_name,
        mimetype=attachment.mime_type,
        size=attachment.size_bytes,
        sha256=attachment.sha256,
        action_id=action.id,
        sync_state="synced"
        if action.state == "succeeded"
        else "needs_attention"
        if action.state == "needs_attention"
        else "pending",
    )
