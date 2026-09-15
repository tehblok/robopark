"""Durable task messages, unified timeline, and staged attachment blobs."""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.models import User
from robopark_api.services.reliable_actions import begin_action
from robopark_api.services.tracker_client import (
    ALLOWED_ATTACHMENT_MIMES,
    MAX_ATTACHMENT_BYTES,
    normalize_attachment_content_type,
)
from robopark_api.task_workflow_models import ReliableAction, TaskAttachment, TaskMessage

_API_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_RANK = {"tracker": 0, "system": 1, "user": 2}


def staged_attachments_root() -> Path:
    url = make_url(get_settings().database_url)
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        return Path(url.database).resolve().parent / "task-attachments"
    return _API_ROOT / "data" / "task-attachments"


def _timestamp(value: object) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    raw = str(value or "").strip()
    if not raw:
        return time.time()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).timestamp()
    except ValueError:
        return time.time()


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def _tracker_row_id(issue_key: str, external_id: str) -> str:
    candidate = f"tracker:{issue_key}:{external_id}"
    if len(candidate) <= 64:
        return candidate
    digest = hashlib.sha256(f"{issue_key}\0{external_id}".encode()).hexdigest()
    return f"tracker:{digest[:48]}"


def append_system_message(
    db: Session,
    *,
    issue_key: str,
    actor: User | str,
    text: str,
    action_id: str | None = None,
) -> TaskMessage:
    now = time.time()
    actor_id = actor.id if isinstance(actor, User) else None
    actor_name = actor.username if isinstance(actor, User) else str(actor)
    row = TaskMessage(
        id=str(uuid4()),
        issue_key=issue_key,
        kind="system",
        author_user_id=actor_id,
        author_name=actor_name,
        text=text,
        action_id=action_id,
        sync_state="pending" if action_id else "saved",
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def append_user_message(
    db: Session,
    *,
    issue_key: str,
    actor: User,
    text: str,
    idempotency_key: str | None,
) -> TaskMessage:
    begun = begin_action(
        db,
        actor=actor,
        resource_type="tracker_issue",
        resource_id=issue_key,
        action="comment",
        idempotency_key=idempotency_key,
        payload={"text": text},
    )
    row = TaskMessage(
        id=str(uuid4()),
        issue_key=issue_key,
        kind="user",
        author_user_id=actor.id,
        author_name=actor.username,
        text=text,
        action_id=begun.row.id,
        sync_state="pending",
        created_at=begun.row.created_at,
        updated_at=begun.row.created_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _validate_filename(filename: str | None) -> str:
    value = (filename or "").strip()
    if (
        not value
        or value != Path(value).name
        or "/" in value
        or "\\" in value
        or ".." in value
        or "\x00" in value
        or len(value.encode("utf-8")) > 240
    ):
        raise ValueError("task_attachment_filename_invalid")
    return value


def _write_staged_blob(blob_name: str, content: bytes) -> Path:
    root = staged_attachments_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / blob_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with suppress(OSError):
            path.unlink()
        raise
    return path


def stage_attachment(
    db: Session,
    *,
    actor: User,
    issue_key: str,
    message: TaskMessage,
    idempotency_key: str | None,
    filename: str | None,
    content: bytes,
    content_type: str | None,
) -> tuple[TaskAttachment, ReliableAction]:
    original_name = _validate_filename(filename)
    if not content:
        raise ValueError("task_attachment_empty")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError("task_attachment_too_large")
    resolved_type = normalize_attachment_content_type(
        filename=original_name, content=content, content_type=content_type
    )
    if not resolved_type or resolved_type not in ALLOWED_ATTACHMENT_MIMES:
        raise ValueError("task_attachment_invalid_type")
    if message.issue_key != issue_key:
        raise LookupError("task_message_not_found")

    attachment_id = str(uuid4())
    blob_name = uuid4().hex
    digest = hashlib.sha256(content).hexdigest()
    begun = begin_action(
        db,
        actor=actor,
        resource_type="tracker_issue",
        resource_id=issue_key,
        action="attach",
        idempotency_key=idempotency_key,
        payload={
            "attachment_id": attachment_id,
            "blob_name": blob_name,
            "filename": original_name,
            "mime_type": resolved_type,
            "sha256": digest,
            "size_bytes": len(content),
        },
    )
    path: Path | None = None
    try:
        path = _write_staged_blob(blob_name, content)
        now = time.time()
        row = TaskAttachment(
            id=attachment_id,
            message_id=message.id,
            blob_name=blob_name,
            original_name=original_name,
            mime_type=resolved_type,
            size_bytes=len(content),
            sha256=digest,
            created_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row, begun.row
    except Exception:
        db.rollback()
        if path is not None:
            with suppress(OSError):
                path.unlink()
        raise


def _sync_state(row: TaskMessage, action: ReliableAction | None) -> str:
    if action is None:
        return row.sync_state
    if action.state == "succeeded":
        return "synced"
    if action.state == "needs_attention":
        return "needs_attention"
    return "pending"


def merge_timeline(db: Session, *, issue_key: str, comments: list[dict]) -> list[dict]:
    local_rows = list(
        db.scalars(
            select(TaskMessage)
            .where(TaskMessage.issue_key == issue_key)
            .order_by(TaskMessage.created_at, TaskMessage.id)
        ).all()
    )
    by_external = {row.external_id: row for row in local_rows if row.external_id}
    external_attachments: dict[str, list[dict]] = {}
    for comment in comments:
        external_id = str(comment.get("id") or "").strip()
        if not external_id:
            continue
        external_attachments[external_id] = list(comment.get("attachments") or [])
        if external_id in by_external:
            continue
        created_at = _timestamp(comment.get("created_at"))
        row = TaskMessage(
            id=_tracker_row_id(issue_key, external_id),
            issue_key=issue_key,
            kind="tracker",
            author_name=str(comment.get("author") or comment.get("author_login") or "Tracker"),
            text=str(comment.get("text") or ""),
            external_id=external_id,
            sync_state="synced",
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(row)
        local_rows.append(row)
        by_external[external_id] = row
    db.commit()

    attachments = (
        list(
            db.scalars(
                select(TaskAttachment).where(
                    TaskAttachment.message_id.in_([row.id for row in local_rows])
                )
            ).all()
        )
        if local_rows
        else []
    )
    local_attachments: dict[str, list[dict]] = {}
    for attachment in attachments:
        local_attachments.setdefault(attachment.message_id, []).append(
            {
                "id": attachment.id,
                "name": attachment.original_name,
                "size": attachment.size_bytes,
                "url": None,
                "mimetype": attachment.mime_type,
            }
        )
    action_ids = [row.action_id for row in local_rows if row.action_id]
    actions = (
        {
            action.id: action
            for action in db.scalars(
                select(ReliableAction).where(ReliableAction.id.in_(action_ids))
            ).all()
        }
        if action_ids
        else {}
    )

    items = [
        {
            "id": row.id,
            "kind": row.kind,
            "author": row.author_name,
            "text": row.text,
            "created_at": _iso(row.created_at),
            "sync_state": _sync_state(row, actions.get(row.action_id)),
            "attachments": external_attachments.get(
                row.external_id or "", local_attachments.get(row.id, [])
            ),
            "_sort": (row.created_at, _SOURCE_RANK[row.kind], row.id),
        }
        for row in local_rows
    ]
    items.sort(key=lambda item: item.pop("_sort"))
    return items
