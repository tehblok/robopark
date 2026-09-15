"""Transactional idempotency and leasing for durable external actions."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import User
from robopark_api.task_workflow_models import ReliableAction

MAX_BATCH_SIZE = 20
LEASE_SECONDS = 60
_NEEDS_ATTENTION_CODES = {
    "401",
    "403",
    "auth",
    "authentication",
    "forbidden",
    "invalid_payload",
    "missing_transition",
    "transition_not_found",
    "unauthorized",
}
_TRANSIENT_CODES = {"429", "network", "timeout"}


@dataclass(frozen=True)
class BeginResult:
    row: ReliableAction
    result: Any | None = None
    created: bool = False


def canonical_payload(payload: Any) -> tuple[str, str]:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError):
        raise HTTPException(400, "reliable_action_payload_invalid") from None
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return encoded, digest


def _query(
    *, actor_id: int, resource_type: str, resource_id: str, action: str, idempotency_key: str
):
    return select(ReliableAction).where(
        ReliableAction.actor_user_id == actor_id,
        ReliableAction.resource_type == resource_type,
        ReliableAction.resource_id == resource_id,
        ReliableAction.action == action,
        ReliableAction.idempotency_key == idempotency_key,
    )


def replay_action(row: ReliableAction, payload_hash: str) -> BeginResult:
    if row.payload_hash != payload_hash:
        raise HTTPException(409, "reliable_action_payload_conflict")
    if row.state == "succeeded":
        result = json.loads(row.result_json) if row.result_json is not None else None
        return BeginResult(row=row, result=result, created=False)
    raise HTTPException(409, "reliable_action_uncertain")


def begin_action(
    db: Session,
    *,
    actor: User,
    resource_type: str,
    resource_id: str,
    action: str,
    idempotency_key: str | None,
    payload: Any,
) -> BeginResult:
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(400, "reliable_action_key_invalid")

    payload_json, payload_hash = canonical_payload(payload)
    query = _query(
        actor_id=actor.id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        idempotency_key=idempotency_key,
    )
    existing = db.scalar(query)
    if existing is not None:
        return replay_action(existing, payload_hash)

    now = time.time()
    row = ReliableAction(
        actor_user_id=actor.id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        payload_json=payload_json,
        state="pending",
        next_attempt_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # The competing transaction owns the durable row. Restore this session
        # before loading it; callers have not yet received a reservation and
        # therefore cannot have attached local state to this action.
        db.rollback()
        db.expire_all()
        existing = db.scalar(query)
        if existing is None:
            raise
        return replay_action(existing, payload_hash)
    return BeginResult(row=row, created=True)


def _result_payload(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


def complete_action(db: Session, row: ReliableAction, result: Any) -> ReliableAction:
    payload_json, _ = canonical_payload(_result_payload(result))
    row.state = "succeeded"
    row.result_json = payload_json
    row.error_code = None
    row.lease_until = None
    row.updated_at = time.time()
    db.flush()
    return row


def mark_needs_attention(
    db: Session,
    row: ReliableAction,
    *,
    error_code: str,
    now: float | None = None,
) -> ReliableAction:
    current = time.time() if now is None else now
    row.state = "needs_attention"
    row.error_code = error_code
    row.attempts += 1
    row.next_attempt_at = current
    row.lease_until = None
    row.updated_at = current
    db.flush()
    return row


def _is_transient(error_code: str) -> bool:
    normalized = error_code.strip().lower()
    if normalized in _TRANSIENT_CODES:
        return True
    try:
        return 500 <= int(normalized) <= 599
    except ValueError:
        return False


def _jitter(row: ReliableAction) -> float:
    seed = f"{row.id}:{row.attempts}".encode()
    return int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") / 2**64


def schedule_retry(
    db: Session,
    row: ReliableAction,
    *,
    error_code: str,
    now: float | None = None,
) -> ReliableAction:
    normalized = error_code.strip().lower()
    if normalized in _NEEDS_ATTENTION_CODES or not _is_transient(normalized):
        return mark_needs_attention(db, row, error_code=error_code, now=now)

    current = time.time() if now is None else now
    row.attempts += 1
    delay = min(300, 2 ** min(row.attempts, 8)) + _jitter(row)
    row.state = "retry_wait"
    row.error_code = error_code
    row.next_attempt_at = current + delay
    row.lease_until = None
    row.updated_at = current
    db.flush()
    return row


def claim_due_batch(
    db: Session,
    *,
    now: float | None = None,
    limit: int = MAX_BATCH_SIZE,
) -> list[ReliableAction]:
    current = time.time() if now is None else now
    due = or_(
        and_(
            ReliableAction.state.in_(("pending", "retry_wait")),
            ReliableAction.next_attempt_at <= current,
        ),
        and_(
            ReliableAction.state == "sending",
            or_(ReliableAction.lease_until.is_(None), ReliableAction.lease_until <= current),
        ),
    )
    rows = list(
        db.scalars(
            select(ReliableAction)
            .where(due)
            .order_by(ReliableAction.next_attempt_at, ReliableAction.id)
            .limit(max(0, min(limit, MAX_BATCH_SIZE)))
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        row.state = "sending"
        row.lease_until = current + LEASE_SECONDS
        row.updated_at = current
    db.commit()
    return rows
