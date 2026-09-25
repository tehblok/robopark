"""Durable, actor-bound receipts for client-generated host-operation UUIDs."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from robopark_api.models import HostOperationStatus
from robopark_api.ops_schemas import public_result
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.ops.jobs import load_job

RETENTION = timedelta(days=7)
MAX_OPERATION_ROWS = 5_000
_SAFE_TOKEN = re.compile(r"^[a-zA-Z0-9_.:-]{1,64}$")


class OperationIdentityConflict(ValueError):
    pass


class OperationRegistryFull(ValueError):
    pass


_NON_OPERATIONAL_FIELDS = {
    "operation_id",
    "confirmation",
    "confirmation_repeat",
    "password",
    "code",
    "totp",
    "recovery_code",
    "authorization",
    "grant",
    "grant_token",
}


def request_digest(payload: object) -> str:
    """Hash only the canonical, non-secret typed operational request."""
    if hasattr(payload, "model_dump"):
        value = payload.model_dump(mode="json")
    elif isinstance(payload, dict):
        value = dict(payload)
    else:
        raise TypeError("invalid_operation_request")
    canonical = {key: value[key] for key in sorted(value) if key not in _NON_OPERATIONAL_FIELDS}
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _safe_token(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value) else fallback


def reserve(
    db: Session,
    *,
    operation_id: str,
    actor_user_id: int,
    kind: str,
    request_digest: str | None = None,
) -> HostOperationStatus:
    with database_idempotency_lock(db, "host-operation-registry-admission"):
        digest = request_digest or sha256(f"legacy:{kind}".encode()).hexdigest()
        existing = db.get(HostOperationStatus, operation_id)
        if existing is not None:
            if (
                existing.actor_user_id != actor_user_id
                or existing.kind != kind
                or existing.request_digest != digest
            ):
                raise OperationIdentityConflict("duplicate_operation_id")
            return existing
        prune(db, commit=False)
        if (
            db.scalar(select(func.count()).select_from(HostOperationStatus)) or 0
        ) >= MAX_OPERATION_ROWS:
            raise OperationRegistryFull("operation_registry_full")
        row = HostOperationStatus(
            operation_id=operation_id,
            actor_user_id=actor_user_id,
            kind=kind,
            request_digest=digest,
            receipt_state="received",
            state="queued",
            phase="request_received",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def mark_accepted(
    db: Session,
    *,
    operation_id: str,
    phase: str = "awaiting_host",
    state: str = "running",
    progress_percent: int | None = None,
) -> HostOperationStatus:
    row = db.get(HostOperationStatus, operation_id)
    if row is None:
        raise LookupError("operation_not_found")
    row.receipt_state = "accepted"
    row.state = state if state in {"queued", "running"} else "running"
    row.phase = _safe_token(phase, "awaiting_host")
    row.progress_percent = progress_percent
    row.error = None
    db.commit()
    db.refresh(row)
    return row


def mark_rejected(db: Session, *, operation_id: str, error: str) -> HostOperationStatus:
    row = db.get(HostOperationStatus, operation_id)
    if row is None:
        raise LookupError("operation_not_found")
    row.receipt_state = "terminal"
    row.state = "failed"
    row.phase = "rejected"
    row.error = _safe_token(error, "operation_rejected")
    row.progress_percent = 100
    row.terminal_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


def update_from_job(
    db: Session,
    *,
    operation_id: str,
    job,
    progress: tuple[str | None, int | None] = (None, None),
) -> HostOperationStatus:
    row = db.get(HostOperationStatus, operation_id)
    if row is None:
        raise LookupError("operation_not_found")
    terminal = job.state in {"succeeded", "failed"}
    row.receipt_state = "terminal" if terminal else "accepted"
    row.state = (
        job.state if job.state in {"queued", "running", "succeeded", "failed"} else "running"
    )
    row.phase = _safe_token(progress[0] or job.phase, "running")
    row.error = _safe_token(job.error, "host_operation_failed") if job.error else None
    projected = public_result(job.extra.get("host_result"))
    row.host_result_json = (
        json.dumps(projected.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        if projected is not None
        else None
    )
    row.progress_percent = progress[1]
    if terminal:
        row.progress_percent = 100 if row.progress_percent is None else row.progress_percent
        row.terminal_at = row.terminal_at or datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


def snapshot_current_job(db: Session, ops_dir: Path) -> HostOperationStatus | None:
    """Persist the current file-slot state before a later job can replace it."""
    job = load_job(ops_dir)
    if job is None or db.get(HostOperationStatus, job.id) is None:
        return None
    return update_from_job(db, operation_id=job.id, job=job)


def prune(
    db: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    """Delete only expired terminal receipts; live/uncertain receipts are retained."""
    current = now or datetime.now(UTC)
    cutoff = current - RETENTION
    result = db.execute(
        delete(HostOperationStatus)
        .where(
            HostOperationStatus.receipt_state == "terminal",
            HostOperationStatus.terminal_at.is_not(None),
            HostOperationStatus.terminal_at < cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    removed = max(0, int(result.rowcount or 0))
    if commit:
        db.commit()
    else:
        db.flush()
    return removed
