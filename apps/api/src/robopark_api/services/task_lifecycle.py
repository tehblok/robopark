"""Atomic local task lifecycle commands and their durable Tracker actions."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import AuditLog, Park, User, UserPark
from robopark_api.services import rbac, tracker_client, tracker_signatures
from robopark_api.services.defect_codes import validate_defect_code
from robopark_api.services.reliable_actions import (
    BeginResult,
    begin_action,
    canonical_payload,
    complete_action,
)
from robopark_api.services.task_timeline import _validate_filename, _write_staged_blob
from robopark_api.services.tracker_claims import claim_issue, get_claim, release_claim
from robopark_api.task_workflow_models import (
    HiddenTask,
    ReliableAction,
    TaskAttachment,
    TaskMessage,
    TaskReview,
)

_IMAGE_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
_REVIEW_ROLES = frozenset({rbac.RoleSlug.OPERATOR, rbac.RoleSlug.ADMIN, rbac.RoleSlug.ROYAL})


def _role(user: User) -> str:
    return rbac.role_slug(user)


def _action(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    action: str,
    idempotency_key: str | None,
    payload: dict,
) -> BeginResult:
    """Begin an action, allowing an identical pending lifecycle command to replay."""
    _, digest = canonical_payload(payload)
    existing = db.scalar(
        select(ReliableAction).where(
            ReliableAction.actor_user_id == actor.id,
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == action,
            ReliableAction.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.payload_hash != digest:
            raise HTTPException(409, "reliable_action_payload_conflict")
        return BeginResult(row=existing, created=False)
    return begin_action(
        db,
        actor=actor,
        resource_type="tracker_issue",
        resource_id=issue_key,
        action=action,
        idempotency_key=idempotency_key,
        payload=payload,
    )


def _message(
    db: Session,
    *,
    issue_key: str,
    actor: User,
    text: str,
    action: ReliableAction,
    kind: str = "system",
) -> TaskMessage:
    existing = db.scalar(select(TaskMessage).where(TaskMessage.action_id == action.id))
    if existing is not None:
        return existing
    row = TaskMessage(
        id=str(uuid4()),
        issue_key=issue_key,
        kind=kind,
        author_user_id=actor.id,
        author_name=actor.username,
        text=text,
        action_id=action.id,
        sync_state="pending",
        created_at=action.created_at,
        updated_at=action.created_at,
    )
    db.add(row)
    db.flush()
    return row


def _active_review(db: Session, issue_key: str) -> TaskReview | None:
    return db.scalar(
        select(TaskReview)
        .where(TaskReview.issue_key == issue_key, TaskReview.state != "closed")
        .order_by(TaskReview.created_at.desc())
    )


def _latest_review(db: Session, issue_key: str) -> TaskReview | None:
    return db.scalar(
        select(TaskReview)
        .where(TaskReview.issue_key == issue_key)
        .order_by(TaskReview.created_at.desc())
    )


def _active_hidden(db: Session, issue_key: str) -> HiddenTask | None:
    return db.scalar(
        select(HiddenTask)
        .where(HiddenTask.issue_key == issue_key, HiddenTask.restored_at.is_(None))
        .order_by(HiddenTask.created_at.desc())
    )


def is_hidden(db: Session, issue_key: str) -> bool:
    return _active_hidden(db, issue_key) is not None


def hidden_issue_keys(db: Session) -> set[str]:
    return set(
        db.scalars(select(HiddenTask.issue_key).where(HiddenTask.restored_at.is_(None))).all()
    )


def _sync_state(db: Session, issue_key: str) -> str:
    states = set(
        db.scalars(
            select(ReliableAction.state).where(
                ReliableAction.resource_type == "tracker_issue",
                ReliableAction.resource_id == issue_key,
            )
        ).all()
    )
    if "needs_attention" in states:
        return "needs_attention"
    if states - {"succeeded"}:
        return "pending"
    return "synced" if states else "saved"


def workflow(
    db: Session,
    *,
    issue_key: str,
    viewer: User,
    issue: dict | None = None,
    include_hidden: bool = False,
) -> dict:
    claim = get_claim(db, issue_key)
    owner = db.get(User, claim.owner_user_id) if claim is not None else None
    review = _latest_review(db, issue_key)
    hidden = _active_hidden(db, issue_key)
    hidden_out = None
    if hidden is not None and include_hidden and rbac.is_admin_or_royal(viewer):
        hidden_actor = db.get(User, hidden.actor_user_id)
        hidden_out = {
            "reason": hidden.reason,
            "actor": hidden_actor.username if hidden_actor is not None else "",
            "created_at": datetime.fromtimestamp(hidden.created_at, UTC).isoformat(),
        }
    display_status = "hidden" if hidden is not None else "queued"
    if hidden is None and review is not None:
        if review.state == "pending":
            display_status = "review"
        elif review.state == "closed":
            display_status = "closed"
        else:
            display_status = "in_progress"
    elif hidden is None and claim is not None:
        display_status = "in_progress"
    queued_at = str((issue or {}).get("created") or "") or None
    return {
        "owner": (
            {"login": owner.username, "display": owner.username}
            if owner is not None and owner.is_active
            else None
        ),
        "review_state": review.state if review is not None else None,
        "display_status": display_status,
        "sync_state": _sync_state(db, issue_key),
        "queued_at": queued_at,
        "queued_at_source": "created_at_estimate" if queued_at else None,
        "hidden": hidden_out,
    }


def _result(
    db: Session,
    *,
    issue_key: str,
    actor: User,
    command: str,
    performed_at: float | None = None,
) -> dict:
    current = workflow(db, issue_key=issue_key, viewer=actor)
    return {
        "key": issue_key,
        "action": command,
        "status": current["display_status"],
        "actor": actor.username,
        "performed_at": datetime.fromtimestamp(performed_at, UTC).isoformat()
        if performed_at is not None
        else datetime.now(UTC).isoformat(),
        "sync_state": current["sync_state"],
        "workflow": current,
    }


def claim(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    park: Park,
    idempotency_key: str | None,
) -> dict:
    if _role(actor) != rbac.RoleSlug.MECHANIC:
        raise HTTPException(403, "task_claim_mechanic_required")
    payload = {"owner_user_id": actor.id, "park_id": park.id}
    begun = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="start",
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if begun.created:
        previous = get_claim(db, issue_key)
        previous_owner = db.get(User, previous.owner_user_id) if previous is not None else None
        claim_issue(
            db,
            actor=actor,
            owner=actor,
            issue_key=issue_key,
            park_id=park.id,
            replace=True,
        )
        text = f"Задача взята в работу: {actor.username}"
        if previous_owner is not None and previous_owner.id != actor.id:
            text = f"Передача смены: {previous_owner.username} → {actor.username}"
        _message(db, issue_key=issue_key, actor=actor, text=text, action=begun.row)
        db.commit()
    return _result(
        db,
        issue_key=issue_key,
        actor=actor,
        command="claim",
        performed_at=begun.row.created_at,
    )


def _target_mechanic(db: Session, *, username: str, park_id: int) -> User:
    target = db.scalar(
        select(User)
        .join(UserPark, UserPark.user_id == User.id)
        .where(
            User.username == username.strip(),
            User.is_active.is_(True),
            UserPark.park_id == park_id,
        )
    )
    if target is None or _role(target) != rbac.RoleSlug.MECHANIC:
        raise HTTPException(400, "task_handoff_mechanic_not_found")
    return target


def handoff(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    assignee: str,
    idempotency_key: str | None,
) -> dict:
    current = get_claim(db, issue_key)
    if current is None:
        raise HTTPException(409, "tracker_issue_claim_required")
    target = _target_mechanic(db, username=assignee, park_id=current.park_id)
    existing = db.scalar(
        select(ReliableAction).where(
            ReliableAction.actor_user_id == actor.id,
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "comment",
            ReliableAction.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        saved_payload = json.loads(existing.payload_json)
        if saved_payload.get("to_user_id") != target.id:
            raise HTTPException(409, "reliable_action_payload_conflict")
        return _result(
            db,
            issue_key=issue_key,
            actor=actor,
            command="handoff",
            performed_at=existing.created_at,
        )
    if _role(actor) == rbac.RoleSlug.MECHANIC and current.owner_user_id != actor.id:
        raise HTTPException(403, "task_handoff_owner_required")
    if _role(actor) not in _REVIEW_ROLES | {rbac.RoleSlug.MECHANIC}:
        raise HTTPException(403, "task_handoff_forbidden")
    previous = db.get(User, current.owner_user_id)
    payload = {"from_user_id": current.owner_user_id, "to_user_id": target.id}
    begun = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="comment",
        idempotency_key=idempotency_key,
        payload={"text": f"Передача смены: {previous.username} → {target.username}", **payload},
    )
    if begun.created:
        claim_issue(
            db,
            actor=actor,
            owner=target,
            issue_key=issue_key,
            park_id=current.park_id,
            replace=True,
        )
        _message(
            db,
            issue_key=issue_key,
            actor=actor,
            text=f"Передача смены: {previous.username} → {target.username}",
            action=begun.row,
        )
        db.commit()
    return _result(
        db,
        issue_key=issue_key,
        actor=actor,
        command="handoff",
        performed_at=begun.row.created_at,
    )


def _valid_current_comment(db: Session, *, issue_key: str, owner_id: int, boundary: float) -> bool:
    return (
        db.scalar(
            select(TaskMessage.id).where(
                TaskMessage.issue_key == issue_key,
                TaskMessage.kind == "user",
                TaskMessage.author_user_id == owner_id,
                TaskMessage.created_at >= boundary,
                TaskMessage.text != "",
            )
        )
        is not None
    )


def _validate_photo(
    filename: str | None, content: bytes, content_type: str | None
) -> tuple[str, str]:
    name = _validate_filename(filename)
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    detected = tracker_client.guess_image_content_type(name, content)
    if not content:
        raise ValueError("task_attachment_empty")
    if len(content) > tracker_client.MAX_ATTACHMENT_BYTES:
        raise ValueError("task_attachment_too_large")
    if declared not in _IMAGE_MIMES or detected not in _IMAGE_MIMES or declared != detected:
        raise ValueError("task_attachment_invalid_type")
    return name, detected


def submit_review(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    defect_code: str,
    filename: str | None,
    content: bytes,
    content_type: str | None,
    comment: str | None,
    operator_login: str,
    idempotency_key: str | None,
) -> dict:
    if _role(actor) != rbac.RoleSlug.MECHANIC:
        raise HTTPException(403, "task_review_mechanic_required")
    claim_row = get_claim(db, issue_key)
    if claim_row is None or claim_row.owner_user_id != actor.id:
        raise HTTPException(409, "tracker_issue_claim_required")
    code = validate_defect_code(defect_code)
    name, mime_type = _validate_photo(filename, content, content_type)
    clean_comment = (comment or "").strip()
    digest = hashlib.sha256(content).hexdigest()
    payload = {"comment": clean_comment, "defect_code": code, "photo_sha256": digest}
    primary = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="review",
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if not primary.created:
        return _result(
            db,
            issue_key=issue_key,
            actor=actor,
            command="submit_review",
            performed_at=primary.row.created_at,
        )
    if not clean_comment and not _valid_current_comment(
        db,
        issue_key=issue_key,
        owner_id=actor.id,
        boundary=claim_row.updated_at,
    ):
        db.rollback()
        raise HTTPException(400, "task_completion_comment_required")

    path: Path | None = None
    try:
        if clean_comment:
            clarification = _action(
                db,
                actor=actor,
                issue_key=issue_key,
                action="comment",
                idempotency_key=idempotency_key,
                payload={"text": clean_comment},
            )
            _message(
                db,
                issue_key=issue_key,
                actor=actor,
                text=clean_comment,
                action=clarification.row,
                kind="user",
            )
        attach = _action(
            db,
            actor=actor,
            issue_key=issue_key,
            action="attach",
            idempotency_key=idempotency_key,
            payload={
                "filename": name,
                "mime_type": mime_type,
                "sha256": digest,
                "size_bytes": len(content),
            },
        )
        automatic = _message(
            db,
            issue_key=issue_key,
            actor=actor,
            text=f"Передано на проверку\nКод дефекта: {code}\nОператор: @{operator_login}",
            action=attach.row,
        )
        blob_name = uuid4().hex
        path = _write_staged_blob(blob_name, content)
        db.add(
            TaskAttachment(
                id=attach.row.id,
                message_id=automatic.id,
                blob_name=blob_name,
                original_name=name,
                mime_type=mime_type,
                size_bytes=len(content),
                sha256=digest,
                created_at=time.time(),
            )
        )
        _action(
            db,
            actor=actor,
            issue_key=issue_key,
            action="set_field",
            idempotency_key=idempotency_key,
            payload={"field": "theDefectCode", "value": code},
        )
        current_review = _active_review(db, issue_key)
        now = time.time()
        if current_review is None:
            current_review = TaskReview(
                id=str(uuid4()),
                issue_key=issue_key,
                state="pending",
                actor_user_id=actor.id,
                created_at=now,
                updated_at=now,
            )
            db.add(current_review)
        elif current_review.state == "returned":
            current_review.state = "pending"
            current_review.actor_user_id = actor.id
            current_review.return_reason = None
            current_review.updated_at = now
        else:
            raise HTTPException(409, "task_review_already_pending")
        db.commit()
    except Exception:
        db.rollback()
        if path is not None:
            with suppress(OSError):
                path.unlink()
        raise
    return _result(
        db,
        issue_key=issue_key,
        actor=actor,
        command="submit_review",
        performed_at=primary.row.created_at,
    )


def return_review(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    reason: str,
    assignee: str | None,
    idempotency_key: str | None,
) -> dict:
    if _role(actor) not in _REVIEW_ROLES:
        raise HTTPException(403, "task_review_operator_required")
    clean_reason = reason.strip()
    if not clean_reason:
        raise HTTPException(400, "task_review_return_reason_required")
    review = _active_review(db, issue_key)
    if review is None:
        raise HTTPException(409, "task_review_not_pending")
    claim_row = get_claim(db, issue_key)
    target = (
        _target_mechanic(db, username=assignee, park_id=claim_row.park_id)
        if assignee is not None and claim_row is not None
        else None
    )
    begun = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="return",
        idempotency_key=idempotency_key,
        payload={
            "assignee": target.username if target is not None else None,
            "reason": clean_reason,
        },
    )
    if begun.created:
        now = time.time()
        review.state = "returned"
        review.reviewer_user_id = actor.id
        review.return_reason = clean_reason
        review.updated_at = now
        if claim_row is not None:
            if target is not None:
                claim_row.owner_user_id = target.id
            claim_row.updated_by_user_id = actor.id
            claim_row.updated_at = now
        comment_action = _action(
            db,
            actor=actor,
            issue_key=issue_key,
            action="comment",
            idempotency_key=idempotency_key,
            payload={"text": f"Возврат с проверки: {clean_reason}"},
        )
        _message(
            db,
            issue_key=issue_key,
            actor=actor,
            text=f"Возврат с проверки: {clean_reason}",
            action=comment_action.row,
        )
        db.commit()
    return _result(
        db,
        issue_key=issue_key,
        actor=actor,
        command="return_review",
        performed_at=begun.row.created_at,
    )


def approve_review(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    idempotency_key: str | None,
) -> dict:
    if _role(actor) not in _REVIEW_ROLES:
        raise HTTPException(403, "task_review_operator_required")
    review = _active_review(db, issue_key)
    if review is None:
        existing = db.scalar(
            select(ReliableAction).where(
                ReliableAction.actor_user_id == actor.id,
                ReliableAction.resource_id == issue_key,
                ReliableAction.action == "close",
                ReliableAction.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return _result(
                db,
                issue_key=issue_key,
                actor=actor,
                command="approve_review",
                performed_at=existing.created_at,
            )
        raise HTTPException(409, "task_review_not_pending")
    begun = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="close",
        idempotency_key=idempotency_key,
        payload={},
    )
    if begun.created:
        now = time.time()
        review.state = "closed"
        review.reviewer_user_id = actor.id
        review.closed_at = now
        review.updated_at = now
        release_claim(db, issue_key)
        db.commit()
    return _result(
        db,
        issue_key=issue_key,
        actor=actor,
        command="approve_review",
        performed_at=begun.row.created_at,
    )


def _audit(db: Session, *, actor: User, park_id: int, issue_key: str, action: str, detail: str):
    db.add(
        AuditLog(
            action=action,
            actor_user_id=actor.id,
            actor_username=actor.username,
            actor_role=_role(actor),
            park_id=park_id,
            target_type="tracker_issue",
            target_id=issue_key,
            outcome="success",
            detail=detail,
        )
    )


def hide(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    park_id: int,
    reason: str,
    idempotency_key: str | None,
) -> dict:
    if not rbac.is_admin_or_royal(actor):
        raise HTTPException(403, "task_hide_manager_required")
    clean_reason = reason.strip()
    if not clean_reason:
        raise HTTPException(400, "task_hide_reason_required")
    begun = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="hide",
        idempotency_key=idempotency_key,
        payload={"park_id": park_id, "reason": clean_reason},
    )
    if begun.created:
        now = time.time()
        db.add(
            HiddenTask(
                id=str(uuid4()),
                issue_key=issue_key,
                park_id=park_id,
                reason=clean_reason,
                actor_user_id=actor.id,
                created_at=now,
                updated_at=now,
            )
        )
        _audit(
            db,
            actor=actor,
            park_id=park_id,
            issue_key=issue_key,
            action="tracker.hide",
            detail=clean_reason,
        )
        complete_action(db, begun.row, {"hidden": True})
        db.commit()
    return _result(
        db,
        issue_key=issue_key,
        actor=actor,
        command="hide",
        performed_at=begun.row.created_at,
    )


def restore(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    idempotency_key: str | None,
) -> dict:
    if not rbac.is_admin_or_royal(actor):
        raise HTTPException(403, "task_hide_manager_required")
    hidden = _active_hidden(db, issue_key)
    if hidden is None:
        existing = db.scalar(
            select(ReliableAction).where(
                ReliableAction.actor_user_id == actor.id,
                ReliableAction.resource_id == issue_key,
                ReliableAction.action == "restore",
                ReliableAction.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return _result(
                db,
                issue_key=issue_key,
                actor=actor,
                command="restore",
                performed_at=existing.created_at,
            )
        raise HTTPException(404, "task_hidden_not_found")
    begun = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="restore",
        idempotency_key=idempotency_key,
        payload={"hidden_id": hidden.id},
    )
    if begun.created:
        now = time.time()
        hidden.restored_by_user_id = actor.id
        hidden.restored_at = now
        hidden.updated_at = now
        _audit(
            db,
            actor=actor,
            park_id=hidden.park_id,
            issue_key=issue_key,
            action="tracker.restore",
            detail=hidden.reason,
        )
        complete_action(db, begun.row, {"hidden": False})
        db.commit()
    return _result(
        db,
        issue_key=issue_key,
        actor=actor,
        command="restore",
        performed_at=begun.row.created_at,
    )


def issue_park(db: Session, issue: dict) -> Park:
    park = tracker_signatures.resolve_park(db, issue)
    if park is None:
        raise HTTPException(409, "tracker_issue_park_required")
    return park
