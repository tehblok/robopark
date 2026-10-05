from __future__ import annotations

import hashlib
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session

from robopark_api.collaboration_models import TrackerClaim
from robopark_api.media_schemas import MediaUploadCreateIn
from robopark_api.models import Park, User, UserPark
from robopark_api.services import (
    platform_settings,
    rbac,
    tracker_cache,
    tracker_client,
    tracker_policy,
)
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.task_timeline import staged_attachments_root
from robopark_api.task_workflow_models import MediaUploadSession

MAX_CHUNK_BYTES = 1024 * 1024
SESSION_TTL_SECONDS = 24 * 60 * 60
COMPLETED_RETENTION_SECONDS = 7 * 24 * 60 * 60
PENDING_DEPENDENCY_RETENTION_SECONDS = 30 * 24 * 60 * 60
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp"}
MAX_UNCONSUMED_SESSIONS_PER_USER = 8
MAX_RETAINED_BYTES_PER_USER = 256 * 1024 * 1024
MAX_UNCONSUMED_SESSIONS_GLOBAL = 256
MAX_RETAINED_BYTES_GLOBAL = 2 * 1024 * 1024 * 1024
MIN_FREE_BYTES_AFTER_RESERVATION = 256 * 1024 * 1024
QUOTA_RETRY_AFTER_SECONDS = 60
_RESERVATION_LOCK_KEY = "media-upload-capacity-v1"


@dataclass(frozen=True)
class StartedUpload:
    row: MediaUploadSession
    status: Literal["active", "reinitialized", "completed"]


@dataclass(frozen=True)
class ConsumedUpload:
    original_name: str
    mime_type: str
    content: bytes


def uploads_root() -> Path:
    return staged_attachments_root() / "resumable"


def _unlink_session_blobs(row: MediaUploadSession) -> bool:
    names = {row.blob_name}
    if not row.completed:
        names.add(f"{row.id}.ready")
    failed = False
    for name in names:
        try:
            (uploads_root() / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            failed = True
    return not failed


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


def _authorize_start(db: Session, actor: User, issue_key: str) -> int:
    _assert_upload_capability(db, actor)
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise HTTPException(503, "tracker_token_not_configured")
    try:
        issue = tracker_cache.get_issue(token=token, key=issue_key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(502, "tracker_upstream_error") from exc
    if issue is None:
        raise HTTPException(404, "media_issue_not_found")
    tracker_policy.enforce_issue_scope(db, actor, issue)
    return _resolve_issue_park(db, issue).id


def _assert_upload_capability(db: Session, actor: User) -> None:
    rbac.assert_approved(actor)
    if not rbac.has_permission(db, actor, rbac.PERMISSION_TRACKER_ATTACH):
        raise HTTPException(403, "tracker_attach_disabled")


def _resolve_issue_park(db: Session, issue: dict) -> Park:
    issue_queue = str(issue.get("queue") or "").strip()
    issue_tags = tracker_policy.issue_tags(issue)
    matches = [
        park
        for park in db.scalars(select(Park).where(Park.is_active.is_(True)))
        if (park.tracker_queue or "").strip() == issue_queue
        and tracker_policy.park_tag_matches(park.tag, issue_tags)
    ]
    if len(matches) != 1:
        raise HTTPException(409, "tracker_issue_park_required")
    return matches[0]


def _claim_park_id(db: Session, actor: User, issue_key: str) -> int | None:
    claim = db.get(TrackerClaim, issue_key)
    if claim is None or claim.owner_user_id != actor.id:
        return None
    park = db.get(Park, claim.park_id)
    if park is None or not park.is_active:
        return None
    return park.id


def _assert_park_scope(db: Session, actor: User, park_id: int) -> None:
    park = db.get(Park, park_id)
    if park is None or not park.is_active:
        raise HTTPException(403, "tracker_issue_out_of_scope")
    if rbac.is_admin_or_royal(actor) or rbac.has_permission(
        db, actor, rbac.PERMISSION_PARKS_MANAGE
    ):
        return
    allowed = db.scalar(
        select(UserPark.user_id).where(
            UserPark.user_id == actor.id,
            UserPark.park_id == park_id,
        )
    )
    if allowed is None:
        raise HTTPException(403, "tracker_issue_out_of_scope")


def _ensure_local_scope(db: Session, actor: User, row: MediaUploadSession) -> None:
    if row.park_id is None:
        row.park_id = _claim_park_id(db, actor, row.issue_key)
        if row.park_id is None:
            raise HTTPException(409, "media_upload_scope_refresh_required")
        db.commit()
    _assert_park_scope(db, actor, row.park_id)


def _retained_usage(db: Session, *, actor_user_id: int | None = None) -> tuple[int, int]:
    statement = select(
        func.count(MediaUploadSession.id).filter(
            MediaUploadSession.dependency_terminal_at.is_(None)
        ),
        func.coalesce(func.sum(MediaUploadSession.size_bytes), 0),
    )
    if actor_user_id is not None:
        statement = statement.where(MediaUploadSession.actor_user_id == actor_user_id)
    count, size_bytes = db.execute(statement).one()
    return int(count), int(size_bytes)


def _unwritten_reserved_bytes(db: Session, *, exclude_session_id: str | None = None) -> int:
    remaining = case(
        (
            MediaUploadSession.size_bytes > MediaUploadSession.received_offset,
            MediaUploadSession.size_bytes - MediaUploadSession.received_offset,
        ),
        else_=0,
    )
    statement = select(func.coalesce(func.sum(remaining), 0)).where(
        MediaUploadSession.completed.is_(False)
    )
    if exclude_session_id is not None:
        statement = statement.where(MediaUploadSession.id != exclude_session_id)
    return int(db.scalar(statement) or 0)


def _ensure_disk_capacity(
    db: Session, size_bytes: int, *, exclude_session_id: str | None = None
) -> None:
    root = uploads_root()
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        filesystem = os.statvfs(root)
    except OSError as exc:
        raise HTTPException(503, "media_storage_unavailable") from exc
    available = filesystem.f_bavail * filesystem.f_frsize
    reserved = _unwritten_reserved_bytes(db, exclude_session_id=exclude_session_id)
    if available - reserved - size_bytes < MIN_FREE_BYTES_AFTER_RESERVATION:
        raise HTTPException(507, "media_storage_capacity_exceeded")


def _reserve_capacity(db: Session, actor: User, size_bytes: int) -> None:
    user_sessions, user_bytes = _retained_usage(db, actor_user_id=actor.id)
    if (
        user_sessions >= MAX_UNCONSUMED_SESSIONS_PER_USER
        or user_bytes + size_bytes > MAX_RETAINED_BYTES_PER_USER
    ):
        raise HTTPException(
            429,
            "media_upload_user_quota_exceeded",
            headers={"Retry-After": str(QUOTA_RETRY_AFTER_SECONDS)},
        )
    global_sessions, global_bytes = _retained_usage(db)
    if (
        global_sessions >= MAX_UNCONSUMED_SESSIONS_GLOBAL
        or global_bytes + size_bytes > MAX_RETAINED_BYTES_GLOBAL
    ):
        raise HTTPException(
            429,
            "media_upload_global_quota_exceeded",
            headers={"Retry-After": str(QUOTA_RETRY_AFTER_SECONDS)},
        )
    _ensure_disk_capacity(db, size_bytes)


def _session(
    db: Session, actor: User, upload_id: str, *, for_update: bool = False
) -> MediaUploadSession:
    _assert_upload_capability(db, actor)
    statement = select(MediaUploadSession).where(
        MediaUploadSession.id == upload_id, MediaUploadSession.actor_user_id == actor.id
    )
    if for_update:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise HTTPException(404, "media_upload_not_found")
    _ensure_local_scope(db, actor, row)
    if not row.completed and row.expires_at <= time.time():
        raise HTTPException(410, "media_upload_expired")
    return row


def start(db: Session, actor: User, payload: MediaUploadCreateIn) -> StartedUpload:
    if payload.mime_type not in ALLOWED_MIMES:
        raise HTTPException(400, "media_invalid_type")
    name = _validate_name(payload.name)
    _assert_upload_capability(db, actor)
    existing = db.scalar(
        select(MediaUploadSession)
        .where(
            MediaUploadSession.actor_user_id == actor.id,
            MediaUploadSession.media_id == payload.media_id,
        )
        .execution_options(populate_existing=True)
    )
    authorized_new = existing is None
    if existing is None:
        authorized_park_id = _authorize_start(db, actor, payload.issue_key)
    elif existing.park_id is not None:
        authorized_park_id = existing.park_id
        _assert_park_scope(db, actor, authorized_park_id)
    else:
        authorized_park_id = _claim_park_id(db, actor, existing.issue_key)
        if authorized_park_id is None:
            authorized_park_id = _authorize_start(db, actor, existing.issue_key)
        else:
            _assert_park_scope(db, actor, authorized_park_id)
    with database_idempotency_lock(db, _RESERVATION_LOCK_KEY):
        return _start_locked(
            db,
            actor,
            payload,
            name=name,
            authorized_new=authorized_new,
            authorized_park_id=authorized_park_id,
        )


def _start_locked(
    db: Session,
    actor: User,
    payload: MediaUploadCreateIn,
    *,
    name: str,
    authorized_new: bool,
    authorized_park_id: int,
) -> StartedUpload:
    existing = db.scalar(
        select(MediaUploadSession)
        .where(
            MediaUploadSession.actor_user_id == actor.id,
            MediaUploadSession.media_id == payload.media_id,
        )
        .execution_options(populate_existing=True)
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
        if existing.park_id is None:
            existing.park_id = authorized_park_id
            db.commit()
            db.refresh(existing)
        elif existing.park_id != authorized_park_id:
            raise HTTPException(409, "media_upload_park_conflict")
        _assert_park_scope(db, actor, existing.park_id)
        requested_dependency = (payload.device_id, payload.dependent_action_id)
        existing_dependency = (existing.dependent_device_id, existing.dependent_action_id)
        if existing_dependency == (None, None):
            if payload.dependent_action_id is not None:
                existing.dependent_device_id = payload.device_id
                existing.dependent_action_id = payload.dependent_action_id
                existing.dependency_bound_at = time.time()
                db.commit()
                db.refresh(existing)
        elif existing_dependency != requested_dependency:
            raise HTTPException(409, "media_dependency_conflict")
        if not existing.completed and existing.expires_at <= time.time():
            if not _unlink_session_blobs(existing):
                raise HTTPException(503, "media_storage_unavailable")
            now = time.time()
            _ensure_disk_capacity(db, existing.size_bytes, exclude_session_id=existing.id)
            existing.received_offset = 0
            existing.blob_name = f"{uuid4().hex}.part"
            existing.updated_at = now
            existing.expires_at = now + SESSION_TTL_SECONDS
            db.commit()
            db.refresh(existing)
            return StartedUpload(existing, "reinitialized")
        if existing.completed:
            ready_path = uploads_root() / existing.blob_name
            try:
                ready_stat = ready_path.stat()
            except FileNotFoundError:
                now = time.time()
                _ensure_disk_capacity(db, existing.size_bytes, exclude_session_id=existing.id)
                existing.received_offset = 0
                existing.blob_name = f"{uuid4().hex}.part"
                existing.completed = False
                existing.completed_at = None
                existing.updated_at = now
                existing.expires_at = now + SESSION_TTL_SECONDS
                db.commit()
                db.refresh(existing)
                return StartedUpload(existing, "reinitialized")
            except OSError as exc:
                raise HTTPException(503, "media_storage_unavailable") from exc
            if not stat.S_ISREG(ready_stat.st_mode):
                raise HTTPException(409, "media_upload_blob_invalid")
            return StartedUpload(existing, "completed")
        return StartedUpload(existing, "active")
    if not authorized_new:
        raise HTTPException(409, "media_upload_reservation_changed")
    _reserve_capacity(db, actor, payload.size_bytes)
    now = time.time()
    row = MediaUploadSession(
        actor_user_id=actor.id,
        park_id=authorized_park_id,
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
        dependent_device_id=payload.device_id,
        dependent_action_id=payload.dependent_action_id,
        dependency_bound_at=now if payload.dependent_action_id is not None else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return StartedUpload(row, "active")


def append_chunk(
    db: Session, actor: User, upload_id: str, offset: int, content: bytes, chunk_sha256: str
) -> int:
    with database_idempotency_lock(db, _RESERVATION_LOCK_KEY):
        return _append_chunk_locked(db, actor, upload_id, offset, content, chunk_sha256)


def authorize_chunk(db: Session, actor: User, upload_id: str) -> None:
    # The append operation rechecks this under the reservation lock. This early
    # check prevents unauthorized requests from making the server read their body.
    _session(db, actor, upload_id)


def _append_chunk_locked(
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
    with database_idempotency_lock(db, _RESERVATION_LOCK_KEY):
        # The shared reservation lock serializes the eligibility read, staged-file
        # rename, and commit with cleanup across processes and database dialects.
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
        try:
            db.commit()
        except Exception:
            # Commit failures can be reported before or after durability. Keep the
            # deterministic ready file in place so a fresh session can distinguish
            # those outcomes and cleanup can reclaim either representation.
            db.rollback()
            raise
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
    with database_idempotency_lock(db, _RESERVATION_LOCK_KEY):
        return _bind_action_dependency_locked(
            db,
            actor,
            media_id=media_id,
            issue_key=issue_key,
            device_id=device_id,
            action_id=action_id,
        )


def _bind_action_dependency_locked(
    db: Session,
    actor: User,
    *,
    media_id: str,
    issue_key: str,
    device_id: str,
    action_id: str,
) -> MediaUploadSession:
    _assert_upload_capability(db, actor)
    row = db.scalar(
        select(MediaUploadSession)
        .where(
            MediaUploadSession.actor_user_id == actor.id,
            MediaUploadSession.media_id == media_id,
            MediaUploadSession.issue_key == issue_key,
            MediaUploadSession.completed.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(409, "media_dependency_pending")
    _ensure_local_scope(db, actor, row)
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


def consume_action_dependency(
    db: Session,
    actor: User,
    *,
    media_id: str,
    issue_key: str,
    device_id: str,
    action_id: str,
) -> ConsumedUpload:
    with database_idempotency_lock(db, _RESERVATION_LOCK_KEY):
        row = _bind_action_dependency_locked(
            db,
            actor,
            media_id=media_id,
            issue_key=issue_key,
            device_id=device_id,
            action_id=action_id,
        )
        content = content_path(row).read_bytes()
        return ConsumedUpload(
            original_name=row.original_name,
            mime_type=row.mime_type,
            content=content,
        )


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
    with database_idempotency_lock(db, _RESERVATION_LOCK_KEY):
        return _cleanup_expired_locked(db, now=now)


def _cleanup_expired_locked(db: Session, *, now: float | None = None) -> int:
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
                            MediaUploadSession.completed_at <= cutoff - COMPLETED_RETENTION_SECONDS,
                        ),
                        MediaUploadSession.dependency_terminal_at
                        <= cutoff - COMPLETED_RETENTION_SECONDS,
                        and_(
                            MediaUploadSession.dependent_action_id.is_not(None),
                            MediaUploadSession.dependency_terminal_at.is_(None),
                            MediaUploadSession.completed_at
                            <= cutoff - PENDING_DEPENDENCY_RETENTION_SECONDS,
                        ),
                    )
                )
            )
        )
    )
    deleted = []
    for row in rows:
        if not _unlink_session_blobs(row):
            continue
        db.delete(row)
        deleted.append(row)
    if deleted:
        db.commit()
    return len(deleted)
