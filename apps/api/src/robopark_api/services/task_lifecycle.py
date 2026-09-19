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
from sqlalchemy import func, select, update
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
_TRANSITION_ACTIONS = frozenset({"start", "review", "return", "close"})


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


def _transition_action(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    action: str,
    idempotency_key: str | None,
    payload: dict,
) -> BeginResult:
    """Append one transition to the issue chain without changing replay payloads."""
    effective_payload = dict(payload)
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
        saved_payload = json.loads(existing.payload_json)
        if "depends_on_action_ids" in saved_payload:
            effective_payload["depends_on_action_ids"] = saved_payload["depends_on_action_ids"]
    else:
        previous = db.scalar(
            select(ReliableAction)
            .where(
                ReliableAction.resource_type == "tracker_issue",
                ReliableAction.resource_id == issue_key,
                ReliableAction.action.in_(_TRANSITION_ACTIONS),
            )
            .order_by(ReliableAction.created_at.desc(), ReliableAction.id.desc())
        )
        if previous is not None:
            effective_payload["depends_on_action_ids"] = [previous.id]
    return _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action=action,
        idempotency_key=idempotency_key,
        payload=effective_payload,
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


def _active_review(db: Session, issue_key: str, *, for_update: bool = False) -> TaskReview | None:
    statement = (
        select(TaskReview)
        .where(TaskReview.issue_key == issue_key, TaskReview.state != "closed")
        .order_by(TaskReview.created_at.desc())
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _advance_pending_review(
    db: Session,
    review: TaskReview,
    *,
    state: str,
    reviewer_user_id: int,
    now: float,
    return_reason: str | None = None,
    closed_at: float | None = None,
) -> None:
    claimed = db.execute(
        update(TaskReview)
        .where(TaskReview.id == review.id, TaskReview.state == "pending")
        .values(
            state=state,
            reviewer_user_id=reviewer_user_id,
            return_reason=return_reason,
            closed_at=closed_at,
            updated_at=now,
        )
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "task_review_not_pending")
    db.refresh(review)


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


def tracker_issue_is_closed(issue: dict | None) -> bool:
    if not issue:
        return False
    status_key = str(issue.get("status_key") or "").strip().lower()
    status_text = str(issue.get("status") or "").strip().lower().replace("ё", "е")
    return status_key in {"closed", "resolved", "cancelled", "canceled"} or status_text in {
        "закрыт", "закрыта", "закрыто", "решен", "решена", "отменен", "отменена"
    }


def reconcile_external_closure(db: Session, issue: dict) -> None:
    """Finish stale local ownership when Tracker itself says the task is closed."""
    if not tracker_issue_is_closed(issue):
        return
    issue_key = str(issue.get("key") or "").strip()
    if not issue_key:
        return
    review = _active_review(db, issue_key, for_update=True)
    claim = get_claim(db, issue_key)
    if review is None and claim is None:
        return
    now = time.time()
    if review is not None:
        review.state = "closed"
        review.closed_at = now
        review.updated_at = now
    park_id = claim.park_id if claim is not None else None
    release_claim(db, issue_key)
    db.add(TaskMessage(
        id=str(uuid4()), issue_key=issue_key, kind="system", author_user_id=None,
        author_name="Tracker", text="Задача закрыта в Трекере; работа в системе завершена.",
        external_id="tracker-external-close", sync_state="synced",
        created_at=now, updated_at=now,
    ))
    db.add(AuditLog(
        action="task.external_close", actor_user_id=None, actor_username="Tracker",
        actor_role="system", park_id=park_id, target_type="tracker_issue",
        target_id=issue_key, outcome="success",
    ))
    db.commit()


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
    if review is not None and review.state == "closed" and claim is not None and claim.updated_at > review.updated_at:
        review = None  # The previous repair cycle must not lock a newly reopened task.
    hidden = _active_hidden(db, issue_key)
    hidden_out = None
    if hidden is not None and include_hidden and rbac.is_admin_or_royal(viewer):
        hidden_actor = db.get(User, hidden.actor_user_id)
        hidden_out = {
            "reason": hidden.reason,
            "actor": hidden_actor.username if hidden_actor is not None else "",
            "created_at": datetime.fromtimestamp(hidden.created_at, UTC).isoformat(),
        }
    display_status = "closed" if tracker_issue_is_closed(issue) else "hidden" if hidden is not None else "queued"
    if display_status != "closed" and hidden is None and review is not None:
        if review.state == "pending":
            display_status = "review"
        elif review.state == "closed":
            # Acceptance by the operator is not proof of the remote final status.
            # Only the authoritative issue status above confirms closure.
            display_status = "closing"
        else:
            display_status = "in_progress"
    elif display_status != "closed" and hidden is None and claim is not None:
        display_status = "in_progress"
    queued_at = str((issue or {}).get("queued_at") or "") or None
    sync_error = db.scalar(select(ReliableAction.error_code).where(
        ReliableAction.resource_type == "tracker_issue",
        ReliableAction.resource_id == issue_key,
        ReliableAction.state == "needs_attention",
    ).order_by(ReliableAction.updated_at.desc()).limit(1))
    # Only stable public reason codes, never upstream exception text or credentials.
    if sync_error not in {"task_already_closed", "tracker_transition_missing", "authentication", "401", "403", "forbidden", "invalid_payload", "prerequisite_failed", "duplicate_remote_action"}:
        sync_error = None
    return {
        "owner": (
            {"login": owner.username, "display": owner.username}
            if owner is not None and owner.is_active
            else None
        ),
        "review_state": review.state if review is not None else None,
        "display_status": display_status,
        "sync_state": _sync_state(db, issue_key),
        "sync_error_code": sync_error,
        "queued_at": queued_at,
        "queued_at_source": "tracker_history" if queued_at else None,
        "hidden": hidden_out,
        "has_current_cycle_comment": bool(
            claim is not None
            and _valid_current_comment(
                db,
                issue_key=issue_key,
                owner_id=claim.owner_user_id,
                boundary=claim.updated_at,
            )
        ),
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
    pending_close = db.scalar(select(ReliableAction.id).where(
        ReliableAction.resource_type == "tracker_issue",
        ReliableAction.resource_id == issue_key,
        ReliableAction.action == "close",
        ReliableAction.state != "succeeded",
    ).limit(1))
    if pending_close is not None:
        raise HTTPException(409, "task_closing_pending")
    payload = {"owner_user_id": actor.id, "park_id": park.id}
    begun = _transition_action(
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
    reason: str,
    idempotency_key: str | None,
    done: str = "",
    remaining: str = "",
    obstacles: str = "",
) -> dict:
    current = get_claim(db, issue_key)
    if current is None:
        raise HTTPException(409, "tracker_issue_claim_required")
    target = _target_mechanic(db, username=assignee, park_id=current.park_id)
    reason = reason.strip()
    if not reason:
        raise HTTPException(400, "task_handoff_reason_required")
    done, remaining, obstacles = done.strip(), remaining.strip(), obstacles.strip()
    previous = db.get(User, current.owner_user_id)
    message_lines = [
        f"Передача смены: {previous.username} → {target.username}",
        f"Причина: {reason}",
    ]
    for label, value in (("Сделано", done), ("Осталось", remaining), ("Препятствия", obstacles)):
        if value:
            message_lines.append(f"{label}: {value}")
    message_text = "\n".join(message_lines)
    payload = {
        "text": message_text,
        "from_user_id": current.owner_user_id,
        "to_user_id": target.id,
        "reason": reason,
        "done": done,
        "remaining": remaining,
        "obstacles": obstacles,
    }
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
        if any(
            saved_payload.get(field) != payload[field]
            for field in ("to_user_id", "reason", "done", "remaining", "obstacles")
        ):
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
    begun = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="comment",
        idempotency_key=idempotency_key,
        payload=payload,
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
            text=message_text,
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
                func.length(func.trim(TaskMessage.text, " \t\r\n")) > 0,
            )
        )
        is not None
    )


def _validate_photo(
    filename: str | None, content: bytes, content_type: str | None
) -> tuple[str, str]:
    name = _validate_filename(filename)
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    if not content:
        raise ValueError("task_attachment_empty")
    if len(content) > tracker_client.MAX_ATTACHMENT_BYTES:
        raise ValueError("task_attachment_too_large")
    detected = None
    if content.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        detected = "image/webp"
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
    dependencies = (["comment"] if clean_comment else []) + ["attach", "set_field"]
    payload = {
        "comment": clean_comment,
        "defect_code": code,
        "depends_on_actions": dependencies,
        "photo_sha256": digest,
    }
    primary = _transition_action(
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
    requested_assignee = assignee.strip() if assignee is not None else None
    existing = db.scalar(
        select(ReliableAction).where(
            ReliableAction.actor_user_id == actor.id,
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "return",
            ReliableAction.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        saved_payload = json.loads(existing.payload_json)
        if (
            saved_payload.get("assignee") != requested_assignee
            or saved_payload.get("reason") != clean_reason
        ):
            raise HTTPException(409, "reliable_action_payload_conflict")
        saved_response = saved_payload.get("local_response")
        if isinstance(saved_response, dict):
            return saved_response
        return _result(
            db,
            issue_key=issue_key,
            actor=actor,
            command="return_review",
            performed_at=existing.created_at,
        )
    review = _active_review(db, issue_key, for_update=True)
    claim_row = get_claim(db, issue_key)
    target = (
        _target_mechanic(db, username=assignee, park_id=claim_row.park_id)
        if assignee is not None and claim_row is not None
        else None
    )
    payload = {
        "assignee": target.username if target is not None else None,
        "reason": clean_reason,
    }
    if review is None or review.state != "pending":
        raise HTTPException(409, "task_review_not_pending")
    now = time.time()
    _advance_pending_review(
        db,
        review,
        state="returned",
        reviewer_user_id=actor.id,
        return_reason=clean_reason,
        now=now,
    )
    begun = _transition_action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="return",
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if begun.created:
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
        local_response = _result(
            db,
            issue_key=issue_key,
            actor=actor,
            command="return_review",
            performed_at=begun.row.created_at,
        )
        saved_payload = json.loads(begun.row.payload_json)
        encoded, digest = canonical_payload({**saved_payload, "local_response": local_response})
        begun.row.payload_json = encoded
        begun.row.payload_hash = digest
        db.commit()
        return local_response
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
    existing = db.scalar(
        select(ReliableAction).where(
            ReliableAction.actor_user_id == actor.id,
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "close",
            ReliableAction.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        saved_payload = json.loads(existing.payload_json)
        saved_response = saved_payload.get("local_response")
        if isinstance(saved_response, dict):
            return saved_response
        return _result(
            db,
            issue_key=issue_key,
            actor=actor,
            command="approve_review",
            performed_at=existing.created_at,
        )
    review = _active_review(db, issue_key, for_update=True)
    if review is None:
        raise HTTPException(409, "task_review_not_pending")
    if review.state != "pending":
        raise HTTPException(409, "task_review_not_pending")
    now = time.time()
    _advance_pending_review(
        db,
        review,
        state="closed",
        reviewer_user_id=actor.id,
        closed_at=now,
        now=now,
    )
    begun = _transition_action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="close",
        idempotency_key=idempotency_key,
        payload={},
    )
    if begun.created:
        release_claim(db, issue_key)
        local_response = _result(
            db,
            issue_key=issue_key,
            actor=actor,
            command="approve_review",
            performed_at=begun.row.created_at,
        )
        saved_payload = json.loads(begun.row.payload_json)
        encoded, digest = canonical_payload({**saved_payload, "local_response": local_response})
        begun.row.payload_json = encoded
        begun.row.payload_hash = digest
        db.commit()
        return local_response
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
