"""Durable, actor-bound receipts for client-generated host-operation UUIDs."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from robopark_api.models import HostOperationStatus
from robopark_api.ops_schemas import public_result

RETENTION = timedelta(days=7)
MAX_OPERATION_ROWS = 5_000
_SAFE_TOKEN = re.compile(r"^[a-zA-Z0-9_.:-]{1,64}$")


class OperationIdentityConflict(ValueError):
    pass


class OperationRegistryFull(ValueError):
    pass


def _safe_token(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value) else fallback


def reserve(
    db: Session, *, operation_id: str, actor_user_id: int, kind: str,
) -> HostOperationStatus:
    existing = db.get(HostOperationStatus, operation_id)
    if existing is not None:
        if existing.actor_user_id != actor_user_id or existing.kind != kind:
            raise OperationIdentityConflict("duplicate_operation_id")
        if existing.receipt_state == "terminal" and existing.phase == "rejected":
            existing.receipt_state = "received"
            existing.state = "queued"
            existing.phase = "request_received"
            existing.error = None
            existing.progress_percent = None
            existing.terminal_at = None
            db.commit()
            db.refresh(existing)
        return existing
    prune(db)
    if (db.scalar(select(func.count()).select_from(HostOperationStatus)) or 0) >= MAX_OPERATION_ROWS:
        raise OperationRegistryFull("operation_registry_full")
    row = HostOperationStatus(
        operation_id=operation_id,
        actor_user_id=actor_user_id,
        kind=kind,
        receipt_state="received",
        state="queued",
        phase="request_received",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def mark_accepted(
    db: Session, *, operation_id: str, phase: str = "awaiting_host",
    state: str = "running", progress_percent: int | None = None,
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
    db: Session, *, operation_id: str, job, progress: tuple[str | None, int | None] = (None, None),
) -> HostOperationStatus:
    row = db.get(HostOperationStatus, operation_id)
    if row is None:
        raise LookupError("operation_not_found")
    terminal = job.state in {"succeeded", "failed"}
    row.receipt_state = "terminal" if terminal else "accepted"
    row.state = job.state if job.state in {"queued", "running", "succeeded", "failed"} else "running"
    row.phase = _safe_token(progress[0] or job.phase, "running")
    row.error = _safe_token(job.error, "host_operation_failed") if job.error else None
    projected = public_result(job.extra.get("host_result"))
    row.host_result_json = (
        json.dumps(projected.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
        if projected is not None else None
    )
    row.progress_percent = progress[1]
    if terminal:
        row.progress_percent = 100 if row.progress_percent is None else row.progress_percent
        row.terminal_at = row.terminal_at or datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


def prune(db: Session, *, now: datetime | None = None) -> int:
    """Delete terminal/abandoned receipts by age, then enforce a hard row ceiling."""
    current = now or datetime.now(UTC)
    cutoff = current - RETENTION
    expired = list(db.scalars(
        select(HostOperationStatus.operation_id)
        .where(HostOperationStatus.updated_at < cutoff)
        .order_by(HostOperationStatus.updated_at, HostOperationStatus.operation_id)
        .limit(500)
    ))
    if expired:
        db.execute(delete(HostOperationStatus).where(HostOperationStatus.operation_id.in_(expired)))
        db.commit()
    overflow = list(db.scalars(
        select(HostOperationStatus.operation_id)
        .where(HostOperationStatus.receipt_state == "terminal")
        .order_by(HostOperationStatus.updated_at.desc(), HostOperationStatus.operation_id.desc())
        .offset(max(0, MAX_OPERATION_ROWS - int(db.scalar(
            select(func.count()).select_from(HostOperationStatus).where(
                HostOperationStatus.receipt_state != "terminal"
            )
        ) or 0)))
        .limit(500)
    ))
    if overflow:
        db.execute(delete(HostOperationStatus).where(HostOperationStatus.operation_id.in_(overflow)))
        db.commit()
    return len(expired) + len(overflow)
