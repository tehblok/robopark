"""One file per kind (UI snapshot, device photo, client log) on a report."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.models import ReportAttachment, User
from robopark_api.services.tracker_client import (
    ALLOWED_ATTACHMENT_MIMES,
    MAX_ATTACHMENT_BYTES,
    guess_image_content_type,
    normalize_attachment_content_type,
)

KIND_UI_SNAPSHOT = "ui_snapshot"
KIND_DEVICE_PHOTO = "device_photo"
KIND_CLIENT_LOG = "client_log"

ATTACHMENT_KINDS = frozenset({KIND_UI_SNAPSHOT, KIND_DEVICE_PHOTO, KIND_CLIENT_LOG})
MAX_LOG_BYTES = 64 * 1024
MAX_FILENAME_BYTES = 240
_FILENAME_RE = re.compile(r"[^\w.\-() ]+", re.UNICODE)
_STORAGE_ERROR = "report attachment storage validation failed"

_API_ROOT = Path(__file__).resolve().parents[2]


class AttachmentStorageError(RuntimeError):
    """Attachment metadata and the persisted file tree do not match."""


class _AttachmentStorageInvalid(RuntimeError):
    """Internal marker for attachment storage validation failures."""


def attachments_root() -> Path:
    settings = get_settings()
    if settings.report_attachments_dir:
        return Path(settings.report_attachments_dir)
    return _API_ROOT / "data" / "report-attachments"


def prune_deleted_report_files(
    *,
    now: float | None = None,
    max_age_seconds: float = 3600,
    max_deletions: int | None = None,
    deadline_monotonic: float | None = None,
) -> int:
    """Retry final unlink of files moved out of a deleted report transaction."""
    staging = attachments_root().resolve() / ".delete-staging"
    if not staging.is_dir() or (max_deletions is not None and max_deletions <= 0):
        return 0
    cutoff = (time.time() if now is None else now) - max_age_seconds
    removed = 0
    for path in staging.iterdir():
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            return removed
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if path.stat().st_mtime <= cutoff:
                path.unlink()
                removed += 1
                if max_deletions is not None and removed >= max(0, max_deletions):
                    return removed
        except OSError:
            continue
    return removed


def _delete_marker(report_id: int) -> Path:
    return attachments_root().resolve() / ".delete-pending" / f"{report_id}.json"


def mark_report_files_for_deletion(report_id: int, storage_keys: list[str]) -> None:
    """Durably record files before DB deletion; files remain live until commit."""
    for key in storage_keys:
        if not isinstance(key, str) or not key.startswith(f"{report_id}/"):
            raise LookupError("report_attachment_not_found")
        _resolve_storage_key(key)
    marker = _delete_marker(report_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".report-delete-", dir=marker.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(storage_keys))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, marker)
        directory = os.open(marker.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with suppress(OSError):
            temporary.unlink()


def clear_report_delete_marker(report_id: int) -> None:
    with suppress(FileNotFoundError):
        _delete_marker(report_id).unlink()


def reconcile_pending_report_deletions(db: Session) -> int:
    """After a crash, keep files if the report survived; otherwise finish deletion."""
    pending = attachments_root().resolve() / ".delete-pending"
    if not pending.is_dir():
        return 0
    removed = 0
    for marker in pending.iterdir():
        if marker.is_symlink() or not re.fullmatch(r"[1-9]\d*\.json", marker.name):
            continue
        report_id = int(marker.stem)
        try:
            if marker.stat().st_size > 65536:
                continue
            keys = json.loads(marker.read_text(encoding="utf-8"))
            if not isinstance(keys, list) or not all(
                isinstance(key, str) and key.startswith(f"{report_id}/") for key in keys
            ):
                continue
            # Referenced keys mean the DB deletion never committed. Comparing
            # keys also handles a database that later reuses the numeric ID.
            if (
                keys
                and db.scalar(
                    select(ReportAttachment.id)
                    .where(ReportAttachment.storage_key.in_(keys))
                    .limit(1)
                )
                is not None
            ):
                marker.unlink()
                continue
            paths = [_resolve_storage_key(key) for key in keys]
            for path in paths:
                path.unlink(missing_ok=True)
                with suppress(OSError):
                    path.parent.rmdir()
            marker.unlink()
            removed += 1
        except (OSError, ValueError, LookupError):
            # Keep the marker for retry; never remove a live report's file.
            continue
    return removed


def sanitize_filename(name: str | None, *, fallback: str) -> str:
    raw = (name or fallback).strip()
    base = raw.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    cleaned = _FILENAME_RE.sub("_", base).strip("._")
    cleaned = cleaned or fallback
    encoded = cleaned.encode("utf-8")
    if len(encoded) <= MAX_FILENAME_BYTES:
        return cleaned

    suffix = Path(cleaned).suffix
    suffix_bytes = suffix.encode("utf-8")
    if len(suffix_bytes) > 16:
        suffix = ""
        suffix_bytes = b""
    stem = cleaned[: -len(suffix)] if suffix else cleaned
    stem_bytes = stem.encode("utf-8")[: MAX_FILENAME_BYTES - len(suffix_bytes)]
    bounded_stem = stem_bytes.decode("utf-8", errors="ignore").rstrip(" ._")
    return f"{bounded_stem}{suffix}" or fallback


def max_bytes_for_kind(kind: str) -> int:
    if kind == KIND_CLIENT_LOG:
        return MAX_LOG_BYTES
    if kind in {KIND_UI_SNAPSHOT, KIND_DEVICE_PHOTO}:
        return MAX_ATTACHMENT_BYTES
    raise ValueError("report_attachment_kind_invalid")


def _resolve_storage_key(storage_key: str) -> Path:
    try:
        root = attachments_root().resolve()
        candidate = (root / storage_key).resolve()
    except RuntimeError:
        raise LookupError("report_attachment_not_found") from None
    if not candidate.is_relative_to(root):
        raise LookupError("report_attachment_not_found")
    return candidate


def _atomic_write(destination: Path, content: bytes) -> None:
    from robopark_api.services.ops.maintenance import require_application_writes

    require_application_writes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".report-upload-",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        require_application_writes()
        os.link(temporary, destination)
    finally:
        with suppress(OSError):
            temporary.unlink()


def _existing_kind(db: Session, report_id: int, kind: str) -> ReportAttachment | None:
    return db.scalar(
        select(ReportAttachment).where(
            ReportAttachment.report_id == report_id,
            ReportAttachment.kind == kind,
        )
    )


def add_attachment(
    db: Session,
    user: User,
    report_id: int,
    *,
    kind: str,
    filename: str | None,
    content: bytes,
    content_type: str | None,
    commit: bool = True,
) -> ReportAttachment:
    from robopark_api.services.reports import _load_report, _require_view

    limit = max_bytes_for_kind(kind)
    report = _load_report(db, report_id)
    _require_view(db, user, report)
    if report.author_user_id != user.id or report.status != "open":
        raise PermissionError("forbidden")
    if not content:
        raise ValueError("report_attachment_empty")
    if len(content) > limit:
        raise ValueError("report_attachment_too_large")
    if _existing_kind(db, report.id, kind) is not None:
        raise ValueError("report_attachment_kind_taken")

    if kind == KIND_CLIENT_LOG:
        resolved_type = "text/plain"
        safe_name = sanitize_filename(filename, fallback="client-log.txt")
        if not safe_name.lower().endswith(".txt"):
            safe_name = sanitize_filename(f"{safe_name}.txt", fallback="client-log.txt")
    else:
        resolved_type = normalize_attachment_content_type(
            filename=filename or "photo.png",
            content=content,
            content_type=content_type,
        ) or guess_image_content_type(filename or "photo.png", content)
        if not resolved_type or resolved_type not in ALLOWED_ATTACHMENT_MIMES:
            raise ValueError("report_attachment_invalid_type")
        fallback = "ui-snapshot.png" if kind == KIND_UI_SNAPSHOT else "device-photo.jpg"
        safe_name = sanitize_filename(filename, fallback=fallback)

    row = ReportAttachment(
        report_id=report.id,
        kind=kind,
        filename=safe_name,
        content_type=resolved_type,
        size_bytes=len(content),
        storage_key="",
    )
    destination: Path | None = None
    placed = False
    try:
        db.add(row)
        db.flush()
        relative = f"{report.id}/{uuid4().hex}"
        destination = _resolve_storage_key(relative)
        _atomic_write(destination, content)
        placed = True
        row.storage_key = relative
        if commit:
            db.commit()
        else:
            db.flush()
    except Exception:
        try:
            db.rollback()
        finally:
            if placed and destination is not None:
                with suppress(OSError):
                    destination.unlink()
        raise

    db.refresh(row)
    return row


def get_attachment(
    db: Session,
    user: User,
    report_id: int,
    attachment_id: int,
) -> tuple[ReportAttachment, Path]:
    from robopark_api.services.reports import _can_view_report, _load_report

    report = _load_report(db, report_id)
    if not _can_view_report(db, user, report):
        raise PermissionError("forbidden")
    row = db.get(ReportAttachment, attachment_id)
    if row is None or row.report_id != report.id:
        raise LookupError("report_attachment_not_found")
    path = _resolve_storage_key(row.storage_key)
    if not path.is_file():
        raise LookupError("report_attachment_not_found")
    return row, path


def _validate_attachment_file(row: ReportAttachment) -> None:
    try:
        path = _resolve_storage_key(row.storage_key)
        if not path.is_file() or path.stat().st_size != row.size_bytes:
            raise _AttachmentStorageInvalid
    except (LookupError, OSError):
        raise _AttachmentStorageInvalid from None


def validate_attachment_storage(db: Session) -> int:
    rows = list(db.scalars(select(ReportAttachment)).all())
    try:
        for row in rows:
            _validate_attachment_file(row)
    except _AttachmentStorageInvalid:
        raise AttachmentStorageError(_STORAGE_ERROR) from None
    return len(rows)
