"""Durable task messages, unified timeline, and staged attachment blobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.models import User
from robopark_api.services.reliable_actions import begin_action, canonical_payload
from robopark_api.services.tracker_client import (
    ALLOWED_ATTACHMENT_MIMES,
    MAX_ATTACHMENT_BYTES,
    normalize_attachment_content_type,
)
from robopark_api.task_workflow_models import ReliableAction, TaskAttachment, TaskMessage

_API_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_RANK = {"tracker": 0, "system": 1, "user": 2}
_ACTION_MARKER_RE = re.compile(
    r"(?m)^\s*surp-action:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*$",
    re.IGNORECASE,
)


def _strip_action_marker(text: object) -> str:
    return _ACTION_MARKER_RE.sub("", str(text or "")).strip()


def _action_marker_id(text: object) -> str | None:
    match = _ACTION_MARKER_RE.search(str(text or ""))
    return match.group(1).lower() if match is not None else None


def _remote_attachments(*groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for attachment in group:
            identity = str(attachment.get("id") or attachment)
            if identity not in seen:
                seen.add(identity)
                merged.append(attachment)
    return merged


def _action_external_id(action: ReliableAction) -> str | None:
    if not action.result_json:
        return None
    try:
        result = json.loads(action.result_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(result, dict):
        return None
    external_id = str(result.get("external_id") or "").strip()
    return external_id or None


def staged_attachments_root() -> Path:
    settings = get_settings()
    if settings.staged_attachments_dir:
        return Path(settings.staged_attachments_dir)
    url = make_url(settings.database_url)
    if url.drivername.startswith("sqlite") and url.database and url.database != ":memory:":
        return Path(url.database).parent / "task-attachments"
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
    if not begun.created:
        existing = db.scalar(select(TaskMessage).where(TaskMessage.action_id == begun.row.id))
        if existing is None:
            raise RuntimeError("task_message_replay_missing")
        return existing
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
    message: TaskMessage | None,
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
    if message is not None and message.issue_key != issue_key:
        raise LookupError("task_message_not_found")

    digest = hashlib.sha256(content).hexdigest()
    payload = {
        "filename": original_name,
        **({"message_id": message.id} if message is not None else {"text": "Фото"}),
        "mime_type": resolved_type,
        "sha256": digest,
        "size_bytes": len(content),
    }
    if idempotency_key:
        existing_action = db.scalar(
            select(ReliableAction).where(
                ReliableAction.actor_user_id == actor.id,
                ReliableAction.resource_type == "tracker_issue",
                ReliableAction.resource_id == issue_key,
                ReliableAction.action == "attach",
                ReliableAction.idempotency_key == idempotency_key,
            )
        )
        if existing_action is not None:
            _, payload_hash = canonical_payload(payload)
            if existing_action.payload_hash != payload_hash:
                raise HTTPException(409, "reliable_action_payload_conflict")
            existing_attachment = db.get(TaskAttachment, existing_action.id)
            if existing_attachment is not None:
                return existing_attachment, existing_action
    begun = begin_action(
        db,
        actor=actor,
        resource_type="tracker_issue",
        resource_id=issue_key,
        action="attach",
        idempotency_key=idempotency_key,
        payload=payload,
    )
    if not begun.created:
        existing = db.get(TaskAttachment, begun.row.id)
        if existing is None:
            raise RuntimeError("task_attachment_replay_missing")
        return existing, begun.row

    attachment_id = begun.row.id
    blob_name = uuid4().hex
    path: Path | None = None
    try:
        path = _write_staged_blob(blob_name, content)
        now = time.time()
        if message is None:
            message = TaskMessage(
                id=str(uuid4()),
                issue_key=issue_key,
                kind="user",
                author_user_id=actor.id,
                author_name=actor.username,
                text="Фото",
                action_id=attachment_id,
                sync_state="pending",
                created_at=begun.row.created_at,
                updated_at=begun.row.created_at,
            )
            db.add(message)
            db.flush()
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


def _insert_tracker_message(db: Session, values: dict) -> None:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        statement = dialect_insert(TaskMessage).values(**values).on_conflict_do_nothing()
        db.execute(statement)
        return
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert

        statement = dialect_insert(TaskMessage).values(**values).on_conflict_do_nothing()
        db.execute(statement)
        return
    try:
        with db.begin_nested():
            db.execute(insert(TaskMessage).values(**values))
    except IntegrityError:
        pass


def merge_timeline(
    db: Session,
    *,
    issue_key: str,
    comments: list[dict],
    tracker_visibility_filter: Callable[[list[dict]], list[dict]] | None = None,
) -> list[dict]:
    if tracker_visibility_filter is not None:
        comments = tracker_visibility_filter(comments)
    local_rows = list(
        db.scalars(
            select(TaskMessage)
            .where(TaskMessage.issue_key == issue_key)
            .order_by(TaskMessage.created_at, TaskMessage.id)
        ).all()
    )
    seen_external = {row.external_id for row in local_rows if row.external_id}
    external_attachments: dict[str, list[dict]] = {}
    marker_ids = {
        marker_id
        for comment in comments
        if (marker_id := _action_marker_id(comment.get("text"))) is not None
    }
    attachment_rows = (
        db.scalars(select(TaskAttachment).where(TaskAttachment.id.in_(marker_ids))).all()
        if marker_ids
        else []
    )
    marker_actions = {
        row.id: row
        for row in db.scalars(
            select(ReliableAction).where(
                ReliableAction.id.in_(marker_ids),
                ReliableAction.resource_type == "tracker_issue",
                ReliableAction.resource_id == issue_key,
                ReliableAction.action == "attach",
            )
        ).all()
    }
    marker_messages = {
        row.id: db.get(TaskMessage, row.message_id)
        for row in attachment_rows
        if row.id in marker_actions
    }
    action_external_attachments: dict[str, list[dict]] = {}
    for comment in comments:
        external_id = str(comment.get("id") or "").strip()
        if not external_id:
            continue
        comment_attachments = list(comment.get("attachments") or [])
        marker_id = _action_marker_id(comment.get("text")) or ""
        marker_message = marker_messages.get(marker_id)
        marker_action = marker_actions.get(marker_id)
        if (
            marker_message is not None
            and marker_message.issue_key == issue_key
            and marker_action is not None
            and _action_external_id(marker_action) == external_id
        ):
            action_external_attachments.setdefault(marker_id, []).extend(comment_attachments)
            continue
        external_attachments[external_id] = comment_attachments
        if external_id in seen_external:
            continue
        created_at = _timestamp(comment.get("created_at"))
        _insert_tracker_message(
            db,
            {
                "id": _tracker_row_id(issue_key, external_id),
                "issue_key": issue_key,
                "kind": "tracker",
                "author_name": str(
                    comment.get("author_login") or comment.get("author") or "Tracker"
                ),
                "text": _strip_action_marker(comment.get("text")),
                "external_id": external_id,
                "sync_state": "synced",
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        seen_external.add(external_id)
    db.commit()
    local_rows = list(
        db.scalars(
            select(TaskMessage)
            .where(TaskMessage.issue_key == issue_key)
            .order_by(TaskMessage.created_at, TaskMessage.id)
        ).all()
    )
    if tracker_visibility_filter is not None:
        persisted = [
            {"id": row.id, "text": row.text, "author_login": row.author_name}
            for row in local_rows
            if row.kind == "tracker"
        ]
        visible_ids = {item["id"] for item in tracker_visibility_filter(persisted)}
        local_rows = [row for row in local_rows if row.kind != "tracker" or row.id in visible_ids]

    attachments = (
        list(
            db.scalars(
                select(TaskAttachment)
                .where(TaskAttachment.message_id.in_([row.id for row in local_rows]))
                .order_by(TaskAttachment.created_at, TaskAttachment.id)
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
            "text": _strip_action_marker(row.text) if row.kind == "tracker" else row.text,
            "created_at": _iso(row.created_at),
            "sync_state": _sync_state(row, actions.get(row.action_id)),
            "attachments": _remote_attachments(
                external_attachments.get(row.external_id or "", []),
                *[
                    action_external_attachments.get(attachment["id"], []) or [attachment]
                    for attachment in local_attachments.get(row.id, [])
                ],
            ),
            "_sort": (row.created_at, _SOURCE_RANK[row.kind], row.id),
        }
        for row in local_rows
    ]
    items.sort(key=lambda item: item.pop("_sort"))
    return items
