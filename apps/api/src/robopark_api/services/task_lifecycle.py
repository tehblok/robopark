"""Atomic local task lifecycle commands and their durable Tracker actions."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from robopark_api.models import AuditLog, Park, User, UserPark
from robopark_api.services import rbac, repair_fields, schedules, tracker_client, tracker_signatures
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.defect_codes import validate_defect_code
from robopark_api.services.reliable_actions import (
    BeginResult,
    begin_action,
    canonical_payload,
    complete_action,
)
from robopark_api.services.task_cycle import last_confirmed_closure_at
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
_REPAIR_FAILURE_CODES = frozenset({"repair_fields_conflict", "repair_component_invalid"})
_REPAIR_SUPERSEDED = "repair_report_superseded"


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
        previous_query = (
            select(ReliableAction)
            .where(
                ReliableAction.resource_type == "tracker_issue",
                ReliableAction.resource_id == issue_key,
                ReliableAction.action.in_(_TRANSITION_ACTIONS),
            )
            .order_by(ReliableAction.created_at.desc(), ReliableAction.id.desc())
        )
        closure_at = last_confirmed_closure_at(db, issue_key)
        if closure_at is not None:
            previous_query = previous_query.where(ReliableAction.created_at > closure_at)
        previous = db.scalar(previous_query)
        if (
            action == "review"
            and previous is not None
            and previous.state == "needs_attention"
            and previous.error_code in {*_REPAIR_FAILURE_CODES, _REPAIR_SUPERSEDED}
        ):
            previous = None
        if previous is not None:
            dependencies = list(effective_payload.get("depends_on_action_ids", []))
            effective_payload["depends_on_action_ids"] = [*dependencies, previous.id]
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
        visibility="participants",
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


def _sync_state(db: Session, issue_key: str, *, after: float | None = None) -> str:
    query = select(ReliableAction.state).where(
        ReliableAction.resource_type == "tracker_issue",
        ReliableAction.resource_id == issue_key,
        or_(
            ReliableAction.error_code.is_(None),
            ReliableAction.error_code != _REPAIR_SUPERSEDED,
        ),
    )
    if after is not None:
        query = query.where(ReliableAction.created_at > after)
    states = set(db.scalars(query).all())
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
        "закрыт",
        "закрыта",
        "закрыто",
        "решен",
        "решена",
        "отменен",
        "отменена",
    }


def reconcile_external_closure(db: Session, issue: dict) -> None:
    """Finish stale local ownership when Tracker itself says the task is closed."""
    if not tracker_issue_is_closed(issue):
        return
    issue_key = str(issue.get("key") or "").strip()
    if not issue_key:
        return
    try:
        with database_idempotency_lock(db, f"tracker-external-close:{issue_key}"):
            _reconcile_external_closure_locked(db, issue_key)
    except HTTPException as exc:
        if exc.status_code != 503 or exc.detail != "idempotency_lock_busy":
            raise
        # Another reconciler owns this key. Its result will be visible on the
        # next read or polling cycle; a busy lock must not stop the worker.


def _reconcile_external_closure_locked(db: Session, issue_key: str) -> None:
    previous_closure = last_confirmed_closure_at(db, issue_key)
    unfinished_query = select(ReliableAction.id).where(
        ReliableAction.resource_type == "tracker_issue",
        ReliableAction.resource_id == issue_key,
        ReliableAction.state.in_(("pending", "sending", "retry_wait", "needs_attention")),
    )
    if previous_closure is not None:
        unfinished_query = unfinished_query.where(ReliableAction.created_at > previous_closure)
    has_current_unfinished_action = db.scalar(unfinished_query.limit(1)) is not None
    message_query = select(TaskMessage.id).where(
        TaskMessage.issue_key == issue_key,
        TaskMessage.kind == "user",
    )
    if previous_closure is not None:
        message_query = message_query.where(TaskMessage.created_at > previous_closure)
    has_current_local_message = db.scalar(message_query.limit(1)) is not None
    close_actions = db.scalars(
        select(ReliableAction)
        .where(
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "close",
            ReliableAction.state.in_(("pending", "retry_wait", "needs_attention")),
        )
        .with_for_update()
    ).all()
    review = _active_review(db, issue_key, for_update=True)
    if review is None:
        closing_review = _latest_review(db, issue_key)
        if (
            closing_review is not None
            and closing_review.state == "closed"
            and closing_review.closed_at is None
        ):
            review = closing_review
    claim = get_claim(db, issue_key)
    for action in close_actions:
        complete_action(db, action, {"already_applied": True, "source": "tracker_closed"})
    if (
        review is None
        and claim is None
        and not has_current_unfinished_action
        and not has_current_local_message
    ):
        if close_actions:
            db.commit()
        return
    now = time.time()
    if review is not None:
        review.state = "closed"
        review.closed_at = now
        review.updated_at = now
    park_id = claim.park_id if claim is not None else None
    release_claim(db, issue_key)
    message_id = str(uuid4())
    db.add(
        TaskMessage(
            id=message_id,
            issue_key=issue_key,
            kind="system",
            author_user_id=None,
            author_name="Tracker",
            text="Задача закрыта в Трекере; работа в системе завершена.",
            external_id=f"tracker-external-close:{message_id}",
            sync_state="synced",
            visibility="participants",
            created_at=now,
            updated_at=now,
        )
    )
    db.add(
        AuditLog(
            action="task.external_close",
            actor_user_id=None,
            actor_username="Tracker",
            actor_role="system",
            park_id=park_id,
            target_type="tracker_issue",
            target_id=issue_key,
            outcome="success",
        )
    )
    from robopark_api.services.ai.learning import stage_safely

    stage_safely(
        db,
        review=review,
        park_id=park_id,
        event_key=message_id,
        closed_at=now,
        previous_closure=previous_closure,
    )
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
    if (
        review is not None
        and review.state == "closed"
        and claim is not None
        and claim.updated_at > review.updated_at
    ):
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
    display_status = (
        "closed" if tracker_issue_is_closed(issue) else "hidden" if hidden is not None else "queued"
    )
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
    closure_at = last_confirmed_closure_at(db, issue_key)
    sync_error_query = (
        select(ReliableAction.action, ReliableAction.error_code)
        .where(
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.state == "needs_attention",
            ReliableAction.error_code != _REPAIR_SUPERSEDED,
        )
        .order_by(
            case((ReliableAction.error_code == "prerequisite_failed", 1), else_=0),
            ReliableAction.updated_at.desc(),
        )
        .limit(1)
    )
    if closure_at is not None:
        sync_error_query = sync_error_query.where(ReliableAction.created_at > closure_at)
    sync_failure = db.execute(sync_error_query).first()
    sync_error = sync_failure.error_code if sync_failure is not None else None
    if (
        sync_failure is not None
        and sync_failure.action == "assign_operator"
        and sync_error == "tracker_error"
    ):
        sync_error = "tracker_operator_assignment_failed"
    # Only stable public reason codes, never upstream exception text or credentials.
    if sync_error not in {
        "task_already_closed",
        "tracker_transition_missing",
        "authentication",
        "401",
        "403",
        "forbidden",
        "invalid_payload",
        "prerequisite_failed",
        "duplicate_remote_action",
        "tracker_operator_assignment_failed",
        "tracker_error",
        "repair_fields_conflict",
        "repair_component_invalid",
        "temporary_component_unavailable",
    }:
        sync_error = None
    return {
        "owner": (
            {"login": owner.username, "display": owner.username}
            if owner is not None and owner.is_active
            else None
        ),
        "review_state": review.state if review is not None else None,
        "display_status": display_status,
        "sync_state": _sync_state(db, issue_key, after=closure_at),
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
    issue: dict | None = None,
    component_ids: list[str] | None = None,
    component_options: list[dict[str, str]] | None = None,
) -> dict:
    with database_idempotency_lock(db, f"tracker-claim:{issue_key.strip()}"):
        return _claim_locked(
            db,
            actor=actor,
            issue_key=issue_key,
            park=park,
            idempotency_key=idempotency_key,
            issue=issue,
            component_ids=component_ids,
            component_options=component_options or [],
        )


def _claim_locked(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    park: Park,
    idempotency_key: str | None,
    issue: dict | None,
    component_ids: list[str] | None,
    component_options: list[dict[str, str]],
) -> dict:
    if _role(actor) != rbac.RoleSlug.MECHANIC:
        raise HTTPException(403, "task_claim_mechanic_required")
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(400, "reliable_action_key_invalid")
    pending_close = db.scalar(
        select(ReliableAction.id)
        .where(
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "close",
            ReliableAction.state != "succeeded",
        )
        .limit(1)
    )
    if pending_close is not None:
        raise HTTPException(409, "task_closing_pending")
    existing_assign = db.scalar(
        select(ReliableAction).where(
            ReliableAction.actor_user_id == actor.id,
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "assign_operator",
            ReliableAction.idempotency_key == idempotency_key,
        )
    )
    existing_start = db.scalar(
        select(ReliableAction).where(
            ReliableAction.actor_user_id == actor.id,
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "start",
            ReliableAction.idempotency_key == idempotency_key,
        )
    )
    previous = get_claim(db, issue_key)
    if previous is not None:
        if previous.owner_user_id != actor.id:
            raise HTTPException(
                409,
                "tracker_issue_claim_pending"
                if previous.state == "pending"
                else "tracker_issue_already_claimed",
            )
        if existing_assign is None:
            original = (
                db.get(ReliableAction, previous.start_action_id)
                if previous.start_action_id
                else None
            )
            return _result(
                db,
                issue_key=issue_key,
                actor=actor,
                command="claim",
                performed_at=original.created_at if original is not None else previous.updated_at,
            )
    operator = None
    if existing_assign is not None:
        try:
            operator_id = json.loads(existing_assign.payload_json)["operator_user_id"]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(409, "reliable_action_payload_conflict") from None
        operator = db.get(User, operator_id)
    if operator is None:
        operator = schedules.resolve_active_operator(
            db, park_id=park.id, allow_off_shift_fallback=False
        )
    assign = (
        _action(
            db,
            actor=actor,
            issue_key=issue_key,
            action="assign_operator",
            idempotency_key=idempotency_key,
            payload={
                "operator_user_id": operator.id,
                "login": tracker_signatures.tracker_identity(operator),
            },
        )
        if operator is not None
        else None
    )
    tag = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="ensure_tag",
        idempotency_key=idempotency_key,
        payload={
            "tag": "diag_complete",
            "depends_on_action_ids": [assign.row.id] if assign else [],
        },
    )
    component = None
    if existing_assign is not None:
        component = db.scalar(
            select(ReliableAction).where(
                ReliableAction.actor_user_id == actor.id,
                ReliableAction.resource_type == "tracker_issue",
                ReliableAction.resource_id == issue_key,
                ReliableAction.action == "ensure_components",
                ReliableAction.idempotency_key == idempotency_key,
            )
        )
    elif not (issue or {}).get("components"):
        component = _action(
            db,
            actor=actor,
            issue_key=issue_key,
            action="ensure_components",
            idempotency_key=idempotency_key,
            payload={
                "policy": "temporary_component",
                "name": repair_fields.TEMPORARY_COMPONENT_NAME,
                "depends_on_action_ids": [tag.row.id],
            },
        ).row
    payload = {
        "owner_user_id": actor.id,
        "park_id": park.id,
        "component_policy": "temporary_unsorted",
        "depends_on_action_ids": [component.id if component is not None else tag.row.id],
    }
    if existing_assign is not None and existing_start is not None:
        try:
            saved_payload = json.loads(existing_start.payload_json)
        except (TypeError, ValueError):
            raise HTTPException(409, "reliable_action_payload_conflict") from None
        if not isinstance(saved_payload, dict):
            raise HTTPException(409, "reliable_action_payload_conflict")
        payload = saved_payload
    begun = _action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="start",
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if begun.created:
        previous_owner = db.get(User, previous.owner_user_id) if previous is not None else None
        try:
            claim_issue(
                db,
                actor=actor,
                owner=actor,
                issue_key=issue_key,
                park_id=park.id,
                replace=True,
                state="pending",
                start_action_id=begun.row.id,
                operator_user_id=operator.id if operator else None,
            )
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc
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
    with database_idempotency_lock(db, f"tracker-claim:{issue_key.strip()}"):
        return _handoff_locked(
            db,
            actor=actor,
            issue_key=issue_key,
            assignee=assignee,
            reason=reason,
            idempotency_key=idempotency_key,
            done=done,
            remaining=remaining,
            obstacles=obstacles,
        )


def _handoff_locked(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    assignee: str,
    reason: str,
    idempotency_key: str | None,
    done: str,
    remaining: str,
    obstacles: str,
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
    if _role(actor) != rbac.RoleSlug.MECHANIC or current.owner_user_id != actor.id:
        raise HTTPException(403, "task_handoff_owner_required")
    if current.state != "active":
        raise HTTPException(409, "tracker_issue_claim_not_active")
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


def _current_comment_action(
    db: Session, *, issue_key: str, owner_id: int, boundary: float
) -> ReliableAction | None:
    return db.scalar(
        select(ReliableAction)
        .join(TaskMessage, TaskMessage.action_id == ReliableAction.id)
        .where(
            TaskMessage.issue_key == issue_key,
            TaskMessage.kind == "user",
            TaskMessage.author_user_id == owner_id,
            TaskMessage.created_at >= boundary,
            func.length(func.trim(TaskMessage.text, " \t\r\n")) > 0,
            ReliableAction.action == "comment",
            ReliableAction.actor_user_id == owner_id,
            ReliableAction.resource_id == issue_key,
        )
        .order_by(TaskMessage.created_at.desc(), TaskMessage.id.desc())
    )


def _valid_current_comment(db: Session, *, issue_key: str, owner_id: int, boundary: float) -> bool:
    return (
        _current_comment_action(db, issue_key=issue_key, owner_id=owner_id, boundary=boundary)
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


def reviewer_for_review_replay(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    idempotency_key: str | None,
) -> User | None:
    """Reuse the persisted assignee when an accepted submission is retried."""
    if not idempotency_key:
        return None
    existing = db.scalar(
        select(ReliableAction.id).where(
            ReliableAction.actor_user_id == actor.id,
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "review",
            ReliableAction.idempotency_key == idempotency_key,
        )
    )
    if existing is None:
        return None
    claim = get_claim(db, issue_key)
    if claim is None or claim.owner_user_id != actor.id or claim.operator_user_id is None:
        return None
    return db.get(User, claim.operator_user_id)


def _supersede_failed_repair_chain(
    db: Session,
    *,
    review_actor_user_id: int,
    issue_key: str,
    superseded_by: str | None,
) -> None:
    failed_fields = db.scalar(
        select(ReliableAction)
        .where(
            ReliableAction.actor_user_id == review_actor_user_id,
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == issue_key,
            ReliableAction.action == "set_repair_fields",
            ReliableAction.state == "needs_attention",
            ReliableAction.error_code.in_(_REPAIR_FAILURE_CODES),
        )
        .order_by(ReliableAction.created_at.desc(), ReliableAction.id.desc())
    )
    if failed_fields is None:
        return
    rows = db.scalars(
        select(ReliableAction).where(
            ReliableAction.actor_user_id == failed_fields.actor_user_id,
            ReliableAction.resource_type == failed_fields.resource_type,
            ReliableAction.resource_id == failed_fields.resource_id,
            ReliableAction.idempotency_key == failed_fields.idempotency_key,
            ReliableAction.state != "succeeded",
        )
    ).all()
    now = time.time()
    for row in rows:
        row.state = "needs_attention"
        row.error_code = _REPAIR_SUPERSEDED
        row.next_attempt_at = now
        row.lease_until = None
        row.updated_at = now
        result = {"superseded": True}
        if superseded_by:
            result["superseded_by"] = superseded_by
        row.result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        messages = db.scalars(select(TaskMessage).where(TaskMessage.action_id == row.id)).all()
        if row.action == "attach":
            attachment = db.get(TaskAttachment, row.id)
            if attachment is not None:
                attached_message = db.get(TaskMessage, attachment.message_id)
                if attached_message is not None and attached_message not in messages:
                    messages.append(attached_message)
        for message in messages:
            message.sync_state = "saved"
            message.updated_at = now
    db.flush()


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
    repair_fields_payload: dict | None = None,
    component_options: list[dict[str, str]] | None = None,
    current_issue: dict | None = None,
    reviewer: User | None = None,
    operator_login: str | None = None,
    idempotency_key: str | None,
    notification_hook: Callable[[Session, float], None] | None = None,
) -> dict:
    if _role(actor) != rbac.RoleSlug.MECHANIC:
        raise HTTPException(403, "task_review_mechanic_required")
    claim_row = get_claim(db, issue_key)
    if claim_row is None or claim_row.owner_user_id != actor.id:
        raise HTTPException(409, "tracker_issue_claim_required")
    if claim_row.state != "active":
        raise HTTPException(409, "tracker_issue_claim_not_active")
    existing_review = (
        db.scalar(
            select(ReliableAction.id).where(
                ReliableAction.actor_user_id == actor.id,
                ReliableAction.resource_type == "tracker_issue",
                ReliableAction.resource_id == issue_key,
                ReliableAction.action == "review",
                ReliableAction.idempotency_key == idempotency_key,
            )
        )
        if idempotency_key is not None
        else None
    )
    current_review = _active_review(db, issue_key)
    if (
        current_review is not None
        and current_review.state != "returned"
        and existing_review is None
    ):
        raise HTTPException(409, "task_review_already_pending")
    if claim_row.operator_user_id is None and idempotency_key is not None:
        existing_assignment = db.scalar(
            select(ReliableAction).where(
                ReliableAction.actor_user_id == actor.id,
                ReliableAction.resource_type == "tracker_issue",
                ReliableAction.resource_id == issue_key,
                ReliableAction.action == "assign_operator",
                ReliableAction.idempotency_key == idempotency_key,
            )
        )
        if existing_assignment is not None:
            try:
                assigned_id = json.loads(existing_assignment.payload_json)["operator_user_id"]
            except (KeyError, TypeError, ValueError):
                raise HTTPException(409, "reliable_action_payload_conflict") from None
            reviewer = db.get(User, assigned_id)
            if reviewer is None:
                raise HTTPException(409, "task_review_operator_unavailable")
    if reviewer is None:
        reviewer = schedules.resolve_active_operator(db, park_id=claim_row.park_id)
    if reviewer is None:
        raise HTTPException(409, "task_review_operator_unavailable")
    reviewer_login = tracker_signatures.tracker_identity(reviewer)
    reviewer_label = f"@{reviewer_login}" if reviewer.tracker_login else reviewer.username
    code = validate_defect_code(defect_code)
    name, mime_type = _validate_photo(filename, content, content_type)
    clean_comment = (comment or "").strip()
    digest = hashlib.sha256(content).hexdigest()
    structured = (
        repair_fields.validate_payload(repair_fields_payload)
        if repair_fields_payload is not None
        else None
    )
    if (
        structured is None
        and current_issue is not None
        and repair_fields.has_temporary_component(current_issue)
    ):
        raise HTTPException(409, "repair_fields_required")
    if structured is not None:
        if (
            current_issue is None
            or repair_fields.field_snapshot(current_issue) != structured["expected"]
        ):
            raise HTTPException(409, "repair_fields_conflict")
        repair_fields.validate_component_selection(
            structured, component_options or [], current_issue=current_issue
        )
    generated_report = (
        repair_fields.report_text(structured, component_options or []) if structured else None
    )
    dependencies = (
        ["comment", "attach", "set_repair_fields"]
        if structured
        else (["comment"] if clean_comment else []) + ["attach", "set_field"]
    )
    prior_comment = None
    if not clean_comment and structured is None and existing_review is None:
        prior_comment = _current_comment_action(
            db,
            issue_key=issue_key,
            owner_id=actor.id,
            boundary=claim_row.updated_at,
        )
        if prior_comment is None:
            raise HTTPException(400, "task_completion_comment_required")
    payload = {
        "comment": clean_comment,
        "defect_code": code,
        "depends_on_actions": dependencies,
        "photo_sha256": digest,
    }
    if structured is not None:
        payload["repair_fields"] = structured
    if prior_comment is not None:
        payload["depends_on_action_ids"] = [prior_comment.id]
    if (
        existing_review is None
        and current_review is not None
        and current_review.state == "returned"
        and current_review.return_reason in _REPAIR_FAILURE_CODES
    ):
        _supersede_failed_repair_chain(
            db,
            review_actor_user_id=current_review.actor_user_id,
            issue_key=issue_key,
            superseded_by=idempotency_key,
        )
    deferred_assignment = None
    if claim_row.operator_user_id != reviewer.id:
        deferred_assignment = _action(
            db,
            actor=actor,
            issue_key=issue_key,
            action="assign_operator",
            idempotency_key=idempotency_key,
            payload={
                "operator_user_id": reviewer.id,
                "login": reviewer_login,
            },
        )
        payload.setdefault("depends_on_action_ids", []).append(deferred_assignment.row.id)
    primary = _transition_action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="review",
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if not primary.created:
        result = _result(
            db,
            issue_key=issue_key,
            actor=actor,
            command="submit_review",
            performed_at=primary.row.created_at,
        )
        persisted_review = _latest_review(db, issue_key)
        result["reviewer_user_id"] = (
            persisted_review.reviewer_user_id if persisted_review is not None else reviewer.id
        )
        return result
    path: Path | None = None
    try:
        if deferred_assignment is not None:
            claim_row.operator_user_id = reviewer.id
        structured_action = None
        if structured is not None:
            structured_action = _action(
                db,
                actor=actor,
                issue_key=issue_key,
                action="set_repair_fields",
                idempotency_key=idempotency_key,
                payload=structured,
            ).row
        if generated_report is not None:
            report_body = generated_report
            if clean_comment:
                report_body = f"{report_body}\nУточнение: {clean_comment}"
            clarification = _action(
                db,
                actor=actor,
                issue_key=issue_key,
                action="comment",
                idempotency_key=idempotency_key,
                payload={
                    "text": report_body,
                    "depends_on_action_ids": [structured_action.id],
                },
            )
            _message(
                db,
                issue_key=issue_key,
                actor=actor,
                text=report_body,
                action=clarification.row,
                kind="system",
            )
        elif clean_comment:
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
                **(
                    {"depends_on_action_ids": [structured_action.id]}
                    if structured_action is not None
                    else {}
                ),
            },
        )
        automatic = _message(
            db,
            issue_key=issue_key,
            actor=actor,
            text=f"Передано на проверку\nКод дефекта: {code}\nОператор: {reviewer_label}",
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
        if structured is None:
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
                reviewer_user_id=reviewer.id,
                created_at=now,
                updated_at=now,
            )
            db.add(current_review)
        elif current_review.state == "returned":
            current_review.state = "pending"
            current_review.actor_user_id = actor.id
            current_review.reviewer_user_id = reviewer.id
            current_review.return_reason = None
            current_review.updated_at = now
        else:
            raise HTTPException(409, "task_review_already_pending")
        if notification_hook is not None:
            notification_hook(db, primary.row.created_at)
        db.commit()
    except Exception:
        db.rollback()
        if path is not None:
            with suppress(OSError):
                path.unlink()
        raise
    result = _result(
        db,
        issue_key=issue_key,
        actor=actor,
        command="submit_review",
        performed_at=primary.row.created_at,
    )
    result["reviewer_user_id"] = reviewer.id
    return result


def return_review(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    reason: str,
    assignee: str | None,
    idempotency_key: str | None,
    notification_hook: Callable[[Session, float], None] | None = None,
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
        if notification_hook is not None:
            notification_hook(db, begun.row.created_at)
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
    closing_claim = get_claim(db, issue_key)
    now = time.time()
    _advance_pending_review(
        db,
        review,
        state="closed",
        reviewer_user_id=actor.id,
        closed_at=None,
        now=now,
    )
    begun = _transition_action(
        db,
        actor=actor,
        issue_key=issue_key,
        action="close",
        idempotency_key=idempotency_key,
        # Approval releases ownership before Tracker confirms closure. Keep
        # the observed park with the durable action for the later event.
        payload={"park_id": closing_claim.park_id} if closing_claim is not None else {},
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
