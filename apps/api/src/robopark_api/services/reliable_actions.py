"""Transactional idempotency and leasing for durable external actions."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import User
from robopark_api.services.task_cycle import last_confirmed_closure_at
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
_TRANSIENT_CODES = {"429", "network", "timeout", "temporary_component_version_conflict"}


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


def _begin_sqlite_transaction(db: Session) -> None:
    if db.get_bind().dialect.name != "sqlite":
        return
    connection = db.connection()
    driver_connection = getattr(connection.connection, "driver_connection", connection.connection)
    if not driver_connection.in_transaction:
        cursor = driver_connection.cursor()
        try:
            cursor.execute("BEGIN")
        finally:
            cursor.close()


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
    with db.no_autoflush:
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
    _begin_sqlite_transaction(db)
    # Keep the caller's pending local writes in the outer transaction. Only
    # the competing reliable-action insert belongs to the savepoint below.
    db.flush()
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
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


def retry_needs_attention(db: Session, *, resource_id: str, now: float | None = None) -> int:
    """Make a task's manual-attention actions immediately retryable.

    Attempts and the last error remain intact as delivery/audit history.
    """
    current = time.time() if now is None else now
    closure_at = last_confirmed_closure_at(db, resource_id)
    query = select(ReliableAction).where(
        ReliableAction.resource_type == "tracker_issue",
        ReliableAction.resource_id == resource_id,
        ReliableAction.state == "needs_attention",
        or_(
            ReliableAction.error_code.is_(None),
            ~ReliableAction.error_code.in_({"task_already_closed", "repair_report_superseded"}),
        ),
    )
    if closure_at is not None:
        query = query.where(ReliableAction.created_at > closure_at)
    rows = db.scalars(query).all()
    for row in rows:
        row.state = "retry_wait"
        row.next_attempt_at = current
        row.lease_until = None
        row.updated_at = current
    db.flush()
    return len(rows)


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
    claimed_ids: list[str] = []
    batch_size = max(0, min(limit, MAX_BATCH_SIZE))
    for _ in range(batch_size):
        candidate = (
            select(ReliableAction.id)
            .where(due)
            .order_by(ReliableAction.next_attempt_at, ReliableAction.id)
            .limit(1)
            .scalar_subquery()
        )
        claimed_id = db.execute(
            update(ReliableAction)
            .where(ReliableAction.id == candidate, due)
            .values(
                state="sending",
                lease_until=current + LEASE_SECONDS,
                updated_at=current,
            )
            .returning(ReliableAction.id)
        ).scalar_one_or_none()
        if claimed_id is None:
            break
        claimed_ids.append(claimed_id)
    db.commit()
    return [db.get(ReliableAction, claimed_id) for claimed_id in claimed_ids]
