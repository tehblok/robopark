from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from robopark_api.media_schemas import MediaUploadCreateIn
from robopark_api.models import User
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.task_timeline import staged_attachments_root
from robopark_api.task_workflow_models import MediaUploadSession

MAX_CHUNK_BYTES = 1024 * 1024
SESSION_TTL_SECONDS = 24 * 60 * 60
COMPLETED_RETENTION_SECONDS = 7 * 24 * 60 * 60
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp"}


def uploads_root() -> Path:
    return staged_attachments_root() / "resumable"


def _validate_name(value: str) -> str:
    name = value.strip()
    if (
        not name
        or name != Path(name).name
        or "/" in name
        or "\\" in name
        or ".." in name
        or "\x00" in name
    ):
        raise HTTPException(400, "media_filename_invalid")
    return name


def _session(
    db: Session, actor: User, upload_id: str, *, for_update: bool = False
) -> MediaUploadSession:
    statement = select(MediaUploadSession).where(
        MediaUploadSession.id == upload_id, MediaUploadSession.actor_user_id == actor.id
    )
    if for_update:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise HTTPException(404, "media_upload_not_found")
    if not row.completed and row.expires_at <= time.time():
        raise HTTPException(410, "media_upload_expired")
    return row


def start(db: Session, actor: User, payload: MediaUploadCreateIn) -> MediaUploadSession:
    if payload.mime_type not in ALLOWED_MIMES:
        raise HTTPException(400, "media_invalid_type")
    name = _validate_name(payload.name)
    existing = db.scalar(
        select(MediaUploadSession).where(
            MediaUploadSession.actor_user_id == actor.id,
            MediaUploadSession.media_id == payload.media_id,
        )
    )
    if existing is not None:
        identity = (
            existing.issue_key,
            existing.original_name,
            existing.mime_type,
            existing.size_bytes,
            existing.sha256,
        )
        if identity != (
            payload.issue_key,
            name,
            payload.mime_type,
            payload.size_bytes,
            payload.sha256,
        ):
            raise HTTPException(409, "media_upload_payload_conflict")
        return existing
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=actor.id,
        media_id=payload.media_id,
        issue_key=payload.issue_key,
        original_name=name,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        sha256=payload.sha256,
        received_offset=0,
        blob_name=f"{uuid4().hex}.part",
        completed=False,
        created_at=now,
        updated_at=now,
        expires_at=now + SESSION_TTL_SECONDS,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def append_chunk(
    db: Session, actor: User, upload_id: str, offset: int, content: bytes, chunk_sha256: str
) -> int:
    row = _session(db, actor, upload_id)
    if row.completed:
        raise HTTPException(409, "media_upload_completed")
    if not content or len(content) > MAX_CHUNK_BYTES:
        raise HTTPException(400, "media_chunk_size_invalid")
    if hashlib.sha256(content).hexdigest() != chunk_sha256.lower():
        raise HTTPException(400, "media_chunk_checksum_invalid")
    if offset < 0 or offset + len(content) > row.size_bytes:
        raise HTTPException(409, "media_offset_invalid")
    root = uploads_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / row.blob_name
    if offset < row.received_offset:
        if offset + len(content) > row.received_offset or not path.exists():
            raise HTTPException(409, "media_offset_invalid")
        with path.open("rb") as handle:
            handle.seek(offset)
            if handle.read(len(content)) != content:
                raise HTTPException(409, "media_chunk_replay_conflict")
        return row.received_offset
    if offset != row.received_offset:
        raise HTTPException(409, "media_offset_invalid")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "r+b") as handle:
            handle.seek(offset)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise
    row.received_offset = offset + len(content)
    row.updated_at = time.time()
    db.commit()
    return row.received_offset


def complete(db: Session, actor: User, upload_id: str) -> MediaUploadSession:
    lock_key = f"media-complete:{actor.id}:{upload_id}"
    with database_idempotency_lock(db, lock_key):
        # PostgreSQL locks the row too; SQLite is protected by the file-backed
        # cross-process idempotency lock. In both cases a duplicate caller sees
        # the committed ready blob instead of racing the rename.
        row = _session(db, actor, upload_id, for_update=True)
        if row.completed:
            return row
        if row.received_offset != row.size_bytes:
            raise HTTPException(409, "media_upload_incomplete")
        path = uploads_root() / row.blob_name
        if not path.is_file():
            raise HTTPException(409, "media_upload_missing")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != row.sha256:
            raise HTTPException(400, "media_checksum_invalid")
        signatures = {
            "image/jpeg": content.startswith(b"\xff\xd8\xff"),
            "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/webp": len(content) >= 12
            and content[:4] == b"RIFF"
            and content[8:12] == b"WEBP",
        }
        if not signatures.get(row.mime_type, False):
            raise HTTPException(400, "media_signature_invalid")
        ready_name = f"{row.id}.ready"
        path.replace(uploads_root() / ready_name)
        row.blob_name = ready_name
        row.completed = True
        row.completed_at = row.updated_at = time.time()
        db.commit()
        return row


def content_path(row: MediaUploadSession) -> Path:
    if not row.completed or not row.blob_name.endswith(".ready"):
        raise HTTPException(409, "media_upload_incomplete")
    path = uploads_root() / row.blob_name
    if not path.is_file():
        raise HTTPException(409, "media_upload_missing")
    return path


def bind_action_dependency(
    db: Session,
    actor: User,
    *,
    media_id: str,
    issue_key: str,
    device_id: str,
    action_id: str,
) -> MediaUploadSession:
    row = db.scalar(
        select(MediaUploadSession)
        .where(
            MediaUploadSession.actor_user_id == actor.id,
            MediaUploadSession.media_id == media_id,
            MediaUploadSession.issue_key == issue_key,
            MediaUploadSession.completed.is_(True),
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(409, "media_dependency_pending")
    requested = (device_id, action_id)
    if (row.dependent_device_id, row.dependent_action_id) == requested:
        return row
    bound_at = time.time()
    changed = db.execute(
        update(MediaUploadSession)
        .where(
            MediaUploadSession.id == row.id,
            MediaUploadSession.dependent_device_id.is_(None),
            MediaUploadSession.dependent_action_id.is_(None),
        )
        .values(
            dependent_device_id=device_id,
            dependent_action_id=action_id,
            dependency_bound_at=bound_at,
        )
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "media_dependency_conflict")
    db.commit()
    db.refresh(row)
    return row


def acknowledge_action_dependency(
    db: Session,
    *,
    actor_user_id: int,
    media_id: str,
    device_id: str,
    action_id: str,
    terminal_at: float | None = None,
) -> None:
    row = db.scalar(
        select(MediaUploadSession).where(
            MediaUploadSession.actor_user_id == actor_user_id,
            MediaUploadSession.media_id == media_id,
            MediaUploadSession.dependent_device_id == device_id,
            MediaUploadSession.dependent_action_id == action_id,
        )
    )
    if row is None or row.dependency_terminal_at is not None:
        return
    row.dependency_terminal_at = terminal_at or time.time()
    db.commit()


def cleanup_expired(db: Session, *, now: float | None = None) -> int:
    cutoff = now or time.time()
    rows = list(
        db.scalars(
            select(MediaUploadSession).where(
                (
                    MediaUploadSession.completed.is_(False)
                    & (MediaUploadSession.expires_at <= cutoff)
                )
                | (
                    MediaUploadSession.completed.is_(True)
                    & or_(
                        and_(
                            MediaUploadSession.dependent_action_id.is_(None),
                            MediaUploadSession.completed_at
                            <= cutoff - COMPLETED_RETENTION_SECONDS,
                        ),
                        MediaUploadSession.dependency_terminal_at
                        <= cutoff - COMPLETED_RETENTION_SECONDS,
                    )
                )
            )
        )
    )
    deleted = []
    for row in rows:
        path = uploads_root() / row.blob_name
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            continue
        db.delete(row)
        deleted.append(row)
    if deleted:
        db.commit()
    return len(deleted)
