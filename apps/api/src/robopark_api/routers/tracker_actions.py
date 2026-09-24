from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import Park, User
from robopark_api.schemas import (
    TaskHandoffIn,
    TaskHideIn,
    TaskReviewReturnIn,
    TrackerActionOut,
    TrackerAssignIn,
    TrackerCommentIn,
    TrackerTransitionIn,
)
from robopark_api.services import (
    audit,
    operator_comment_notifications,
    rbac,
    reliable_actions,
    schedules,
    task_lifecycle,
    tracker_cache,
    tracker_client,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_signatures as sig_svc
from robopark_api.services import tracker_submissions as submissions
from robopark_api.services.login_throttle import client_ip
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.tracker_claims import mechanic_owns_issue
from robopark_api.services.tracker_policy import ensure_action_allowed
from robopark_api.task_workflow_models import HiddenTask

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


def _get_issue_or_404(token: str, key: str, *, fresh: bool = False) -> dict:
    try:
        issue = (tracker_client.get_issue if fresh else tracker_cache.get_issue)(
            token=token, key=key
        )
    except tracker_client.TrackerError as exc:
        if not fresh:
            raise
        raise HTTPException(status_code=502, detail="tracker_upstream_error") from exc
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return issue


def _authorize(db: Session, user: User, issue: dict, action: str, request: Request) -> None:
    """Check the policy and audit a denial before propagating it."""
    try:
        if task_lifecycle.is_hidden(db, str(issue.get("key") or "")):
            raise HTTPException(status_code=404)
        ensure_action_allowed(db, user, issue, action)
        if (
            user.role == RoleSlug.MECHANIC
            and action != "assign"
            and not mechanic_owns_issue(db, user, issue)
        ):
            raise HTTPException(status_code=409, detail="tracker_issue_claim_required")
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
    _mark_park_change(request, db, issue)


def _mark_park_change(request: Request, db: Session, issue: dict) -> None:
    tags = sig_svc.issue_tags(issue)
    park_ids = set(db.scalars(select(Park.id).where(Park.tag.in_(tags)))) if tags else set()
    if not park_ids:
        queue = str(issue.get("queue") or "").strip()
        if queue:
            park_ids = set(db.scalars(select(Park.id).where(Park.tracker_queue == queue)))
    if park_ids:
        scopes = set(getattr(request.state, "change_scopes", ()))
        scopes.update(f"work:park:{park_id}" for park_id in park_ids)
        request.state.change_scopes = tuple(scopes)


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
        actor_login=ctx.actor_login,
    )


def _publish_bot_note(token: str, db: Session, user: User, issue: dict, body: str) -> None:
    """Accountability publication is best-effort and never blocks local work."""
    try:
        tracker_client.add_comment(
            token=token,
            key=str(issue.get("key") or ""),
            text=_signed_tracker_text(db, user, issue, body),
        )
    except tracker_client.TrackerError:
        # The local audit remains authoritative; a transient external failure
        # must not strand the task between shifts again.
        return


def _mutation_lease(
    key: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _ensure_tracker_user(user, db)
    action = request.url.path.rsplit("/", 1)[-1]
    if task_lifecycle.is_hidden(db, key):
        raise HTTPException(status_code=404)
    if action == "close" and user.role == RoleSlug.MECHANIC:
        raise HTTPException(403, "task_review_operator_required")
    issue = _get_issue_or_404(_require_token(db), key)
    _authorize(db, user, issue, "attach" if action == "attachments" else action, request)
    with submissions.task_mutation_lease(db, key):
        yield


def _lifecycle_issue(
    db: Session,
    user: User,
    key: str,
    *,
    request: Request,
    actions: tuple[str, ...] = (),
    fresh: bool = False,
) -> dict:
    _ensure_tracker_user(user, db)
    if task_lifecycle.is_hidden(db, key):
        raise HTTPException(status_code=404)
    issue = _get_issue_or_404(_require_token(db), key, fresh=fresh)
    from robopark_api.services.tracker_policy import enforce_issue_scope

    enforce_issue_scope(db, user, issue)
    for action in actions:
        ensure_action_allowed(db, user, issue, action)
    _mark_park_change(request, db, issue)
    return issue


@router.post("/issues/{key}/claim", response_model=TrackerActionOut)
def claim_task(
    key: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    issue = _lifecycle_issue(db, user, key, request=request, actions=("assign",), fresh=True)
    if task_lifecycle.tracker_issue_is_closed(issue):
        raise HTTPException(status_code=409, detail="task_already_closed")
    with submissions.task_mutation_lease(db, key):
        return TrackerActionOut(
            **task_lifecycle.claim(
                db,
                actor=user,
                issue_key=key,
                park=task_lifecycle.issue_park(db, issue),
                idempotency_key=idempotency_key,
                issue=issue,
            )
        )


@router.post("/issues/{key}/handoff", response_model=TrackerActionOut)
def handoff_task(
    key: str,
    payload: TaskHandoffIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _lifecycle_issue(db, user, key, request=request, actions=("comment",))
    with submissions.task_mutation_lease(db, key):
        return TrackerActionOut(
            **task_lifecycle.handoff(
                db,
                actor=user,
                issue_key=key,
                assignee=payload.assignee,
                reason=payload.reason,
                done=payload.done,
                remaining=payload.remaining,
                obstacles=payload.obstacles,
                idempotency_key=idempotency_key,
            )
        )


@router.post("/issues/{key}/submit-review", response_model=TrackerActionOut)
def submit_task_review(
    key: str,
    request: Request,
    defect_code: str = Form(...),
    photo: list[UploadFile] = File(...),
    comment: str | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    issue = _lifecycle_issue(
        db, user, key, request=request, actions=("comment", "attach", "transition")
    )
    park = task_lifecycle.issue_park(db, issue)
    reviewer = schedules.resolve_active_operator(db, park_id=park.id)
    if reviewer is None:
        raise HTTPException(409, "task_review_operator_unavailable")
    if len(photo) != 1:
        raise HTTPException(400, "task_review_exactly_one_photo")
    upload = photo[0]
    content = upload.file.read(tracker_client.MAX_ATTACHMENT_BYTES + 1)
    with submissions.task_mutation_lease(db, key):
        try:
            result = task_lifecycle.submit_review(
                db,
                actor=user,
                issue_key=key,
                defect_code=defect_code,
                filename=upload.filename,
                content=content,
                content_type=upload.content_type,
                comment=comment,
                reviewer=reviewer,
                idempotency_key=idempotency_key,
                notification_hook=lambda tx, performed_at: (
                    request.app.state.push_service.emit_in_transaction(
                        tx,
                        event_type="review_task",
                        park_id=park.id,
                        protected_text=f"Задача {key} ожидает проверки",
                        target_user_ids={reviewer.id},
                        event_key=f"review:{key}:{idempotency_key or datetime.fromtimestamp(performed_at, UTC).isoformat()}",
                    )
                ),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    result.pop("reviewer_user_id", None)
    return TrackerActionOut(**result)


@router.post("/issues/{key}/review/return", response_model=TrackerActionOut)
def return_task_review(
    key: str,
    payload: TaskReviewReturnIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    issue = _lifecycle_issue(db, user, key, request=request, actions=("comment", "transition"))
    park = task_lifecycle.issue_park(db, issue)

    def persist_return_notification(tx: Session, performed_at: float) -> None:
        claim = task_lifecycle.get_claim(tx, key)
        request.app.state.push_service.emit_in_transaction(
            tx,
            event_type="return",
            park_id=park.id,
            protected_text=f"Задача {key} возвращена с проверки",
            target_user_ids={claim.owner_user_id} if claim is not None else None,
            event_key=f"return:{key}:{idempotency_key or datetime.fromtimestamp(performed_at, UTC).isoformat()}",
        )

    with submissions.task_mutation_lease(db, key):
        result = task_lifecycle.return_review(
            db,
            actor=user,
            issue_key=key,
            reason=payload.reason,
            assignee=payload.assignee,
            idempotency_key=idempotency_key,
            notification_hook=persist_return_notification,
        )
    return TrackerActionOut(**result)


@router.post("/issues/{key}/review/approve", response_model=TrackerActionOut)
def approve_task_review(
    key: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    _lifecycle_issue(db, user, key, request=request, actions=("close",))
    with submissions.task_mutation_lease(db, key):
        return TrackerActionOut(
            **task_lifecycle.approve_review(
                db,
                actor=user,
                issue_key=key,
                idempotency_key=idempotency_key,
            )
        )


@router.post("/issues/{key}/retry-now", response_model=TrackerActionOut)
def retry_task_now(
    key: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    if not rbac.is_admin_or_royal(user):
        raise HTTPException(403, "task_retry_manager_required")
    issue = _lifecycle_issue(db, user, key, request=request)
    with submissions.task_mutation_lease(db, key):
        begin = reliable_actions.begin_action(
            db,
            actor=user,
            resource_type="task_control",
            resource_id=key,
            action="retry_now",
            idempotency_key=idempotency_key,
            payload={},
        )
        if begin.result is not None:
            return TrackerActionOut(**begin.result)
        changed = reliable_actions.retry_needs_attention(db, resource_id=key)
        if not changed:
            raise HTTPException(409, "task_retry_not_needed")
        workflow = task_lifecycle.workflow(db, issue_key=key, viewer=user, issue=issue)
        result = TrackerActionOut(
            key=key,
            action="retry_now",
            status="retrying",
            actor=user.username,
            performed_at=datetime.fromtimestamp(begin.row.created_at, UTC).isoformat(),
            sync_state=workflow["sync_state"],
            workflow=workflow,
        )
        reliable_actions.complete_action(db, begin.row, result)
        audit.record(
            db,
            action="tracker.retry_now",
            actor=user,
            target_type="tracker_issue",
            target_id=key,
            detail=f"actions={changed}",
            client_ip=client_ip(request),
        )
        return result


@router.post("/issues/{key}/hide", response_model=TrackerActionOut)
def hide_task(
    key: str,
    payload: TaskHideIn,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    if not rbac.is_admin_or_royal(user):
        raise HTTPException(403, "task_hide_manager_required")
    issue = _lifecycle_issue(db, user, key, request=request)
    with submissions.task_mutation_lease(db, key):
        return TrackerActionOut(
            **task_lifecycle.hide(
                db,
                actor=user,
                issue_key=key,
                park_id=task_lifecycle.issue_park(db, issue).id,
                reason=payload.reason,
                idempotency_key=idempotency_key,
            )
        )


@router.delete("/issues/{key}/hide", response_model=TrackerActionOut)
def restore_task(
    key: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    if not rbac.is_admin_or_royal(user):
        raise HTTPException(403, "task_hide_manager_required")
    hidden = db.scalar(
        select(HiddenTask).where(HiddenTask.issue_key == key, HiddenTask.restored_at.is_(None))
    )
    if hidden is not None:
        request.state.change_scopes = (f"work:park:{hidden.park_id}",)
    with submissions.task_mutation_lease(db, key):
        return TrackerActionOut(
            **task_lifecycle.restore(
                db,
                actor=user,
                issue_key=key,
                idempotency_key=idempotency_key,
            )
        )


@router.post(
    "/issues/{key}/comment",
    response_model=TrackerActionOut,
    response_model_exclude_unset=True,
    response_model_exclude_defaults=True,
    dependencies=[Depends(_mutation_lease)],
)
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
    park = sig_svc.resolve_park(db, issue)
    claim = task_lifecycle.get_claim(db, key) if user.role == RoleSlug.OPERATOR else None
    notification_intent = None
    if user.role == RoleSlug.OPERATOR:
        audience = schedules.eligible_recipients(
            schedules.RoutingEvent(
                db,
                "operator_comment",
                park.id if park is not None else None,
                {claim.owner_user_id} if claim is not None else None,
            ),
            datetime.now(UTC),
        )
        notification_intent = {
            "event_type": "operator_comment",
            "park_id": park.id if park is not None else None,
            "recipient_user_ids": [recipient.id for recipient in audience],
            "protected_text": f"Оператор прокомментировал задачу {key}",
            "tracker_text": signed_text,
            "tracker_status": str(issue.get("status") or "updated"),
        }
    submission, saved = submissions.begin(
        db,
        user,
        key,
        "comment",
        request,
        payload.model_dump(),
        token,
        notification_intent=notification_intent,
    )
    if saved is not None:
        if submission is not None:
            operator_comment_notifications.stage_if_intended(
                db, submission, push_service=request.app.state.push_service
            )
            db.commit()
        return saved

    try:
        tracker_client.add_comment(token=token, key=key, text=signed_text)
    except tracker_client.TrackerError as exc:
        submissions.uncertain(db, submission)
        raise _upstream_error(db, user, "comment", key, exc, request) from exc
    tracker_cache.invalidate_issue(key)

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
    result = _ok(key, "comment", user, issue)
    if user.role == RoleSlug.OPERATOR:
        if submission is not None:
            operator_comment_notifications.stage_if_intended(
                db, submission, push_service=request.app.state.push_service
            )
        else:
            request.app.state.push_service.emit_in_transaction(
                db,
                event_type="operator_comment",
                park_id=park.id if park is not None else None,
                protected_text=f"Оператор прокомментировал задачу {key}",
                target_user_ids={claim.owner_user_id} if claim is not None else None,
                event_key=f"operator-comment:{key}:{result.performed_at}",
            )
    result = submissions.finish(db, submission, result)
    if submission is None:
        # Legacy unkeyed calls have no local submission row; their durable
        # notification still commits before the HTTP response returns.
        db.commit()
    return result


@router.post(
    "/issues/{key}/attachments",
    response_model=TrackerActionOut,
    response_model_exclude_unset=True,
    response_model_exclude_defaults=True,
    dependencies=[Depends(_mutation_lease)],
)
def attach_file(
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

    # This route uses synchronous Tracker HTTP and SQLAlchemy throughout.
    # Run it in FastAPI's worker pool, and bound the in-memory upload copy.
    content = file.file.read(tracker_client.MAX_ATTACHMENT_BYTES + 1)
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
    submission, saved = submissions.begin(
        db,
        user,
        key,
        "attach",
        request,
        {
            "filename": filename,
            "content_type": content_type,
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        token,
    )
    if saved is not None:
        return saved

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
        submissions.uncertain(db, submission)
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
    return submissions.finish(db, submission, _ok(key, "attach", user, issue))


@router.post(
    "/issues/{key}/assign",
    response_model=TrackerActionOut,
    response_model_exclude_unset=True,
    response_model_exclude_defaults=True,
    dependencies=[Depends(_mutation_lease)],
)
def assign_issue(
    key: str,
    payload: TrackerAssignIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    """Retired compatibility route: use the durable /claim or /handoff command."""
    raise HTTPException(status_code=409, detail="task_workflow_required")


@router.post(
    "/issues/{key}/unassign",
    response_model=TrackerActionOut,
    response_model_exclude_unset=True,
    response_model_exclude_defaults=True,
    dependencies=[Depends(_mutation_lease)],
)
def unassign_issue(
    key: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    """Unassign without a handoff would lose the local owner and bot audit."""
    raise HTTPException(status_code=409, detail="task_workflow_required")


@router.post(
    "/issues/{key}/transition",
    response_model=TrackerActionOut,
    response_model_exclude_unset=True,
    response_model_exclude_defaults=True,
    dependencies=[Depends(_mutation_lease)],
)
def transition_issue(
    key: str,
    payload: TrackerTransitionIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    raise HTTPException(status_code=409, detail="tracker_manual_transition_disabled")


@router.post(
    "/issues/{key}/close",
    response_model=TrackerActionOut,
    response_model_exclude_unset=True,
    response_model_exclude_defaults=True,
    dependencies=[Depends(_mutation_lease)],
)
def close_issue(
    key: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerActionOut:
    return TrackerActionOut(
        **task_lifecycle.approve_review(
            db,
            actor=user,
            issue_key=key,
            idempotency_key=idempotency_key,
        )
    )
