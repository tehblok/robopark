"""Durable, idempotent cleanup for unreferenced managed inventory photos."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import (
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryPart,
    InventoryPhotoCleanup,
)


def enqueue(db: Session, storage_keys: set[str]) -> None:
    for storage_key in sorted(storage_keys):
        if not storage_key or "/" in storage_key or "\\" in storage_key or ".." in storage_key:
            raise ValueError("inventory_photo_storage_key_invalid")
        if db.get(InventoryPhotoCleanup, storage_key) is None:
            db.add(InventoryPhotoCleanup(storage_key=storage_key))
    db.flush()


def _is_referenced(db: Session, storage_key: str) -> bool:
    return any(
        db.scalar(select(model.id).where(model.photo_storage_key == storage_key).limit(1))
        is not None
        for model in (InventoryCatalogComponent, InventoryCatalogPart, InventoryPart)
    )


def _unlink_storage_key(storage_key: str) -> None:
    from robopark_api.services.inventory import photos_root

    root = photos_root().resolve()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("inventory_photo_storage_key_invalid")
    Path(path).unlink(missing_ok=True)


def process_pending(db: Session, *, storage_keys: set[str] | None = None) -> int:
    statement = select(InventoryPhotoCleanup).order_by(InventoryPhotoCleanup.created_at)
    if storage_keys is not None:
        if not storage_keys:
            return 0
        statement = statement.where(InventoryPhotoCleanup.storage_key.in_(storage_keys))
    rows = list(db.scalars(statement))
    completed = 0
    for row in rows:
        if _is_referenced(db, row.storage_key):
            db.delete(row)
            completed += 1
            continue
        try:
            _unlink_storage_key(row.storage_key)
        except (OSError, ValueError) as exc:
            row.attempts += 1
            row.last_error = type(exc).__name__
            continue
        db.delete(row)
        completed += 1
    db.commit()
    return completed


def cleanup_pending(db: Session, storage_keys: set[str]) -> bool:
    if not storage_keys:
        return False
    try:
        process_pending(db, storage_keys=storage_keys)
        return (
            db.scalar(
                select(InventoryPhotoCleanup.storage_key)
                .where(InventoryPhotoCleanup.storage_key.in_(storage_keys))
                .limit(1)
            )
            is not None
        )
    except Exception:  # The business commit already succeeded; keep the durable marker.
        db.rollback()
        return True


def recover_ambiguous_blob(bind, storage_key: str) -> bool:
    """Persist cleanup separately, then keep or unlink based on committed references."""
    with Session(bind=bind) as recovery:
        enqueue(recovery, {storage_key})
        recovery.commit()
        return cleanup_pending(recovery, {storage_key})
