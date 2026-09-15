"""Lease-owned cleanup for bounded shared cache state."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import SessionLocal
from robopark_api.services.diagnostic_unknowns import prune_diagnostic_unknowns
from robopark_api.services.live_merge import get_live_merge_store
from robopark_api.services.ops.maintenance import host_maintenance_active
from robopark_api.services.task_timeline import staged_attachments_root
from robopark_api.task_workflow_models import ReliableAction, TaskAttachment

logger = logging.getLogger(__name__)

_RETENTION_BATCH_SIZE = 500
_SUCCESS_RETENTION_SECONDS = 30 * 86400
_UPLOADED_BLOB_RETENTION_SECONDS = 7 * 86400


def prune_tracker_outbox(db: Session, *, now: float) -> tuple[int, int]:
    """Bound reliable-action rows and uploaded staging blobs without losing audit."""
    attachments = list(
        db.scalars(
            select(TaskAttachment)
            .where(
                TaskAttachment.uploaded_at.is_not(None),
                TaskAttachment.uploaded_at < now - _UPLOADED_BLOB_RETENTION_SECONDS,
            )
            .order_by(TaskAttachment.uploaded_at, TaskAttachment.id)
            .limit(_RETENTION_BATCH_SIZE)
        ).all()
    )
    blob_names = [attachment.blob_name for attachment in attachments]
    for attachment in attachments:
        db.delete(attachment)

    actions = list(
        db.scalars(
            select(ReliableAction)
            .where(
                ReliableAction.state == "succeeded",
                ReliableAction.updated_at < now - _SUCCESS_RETENTION_SECONDS,
            )
            .order_by(ReliableAction.updated_at, ReliableAction.id)
            .limit(_RETENTION_BATCH_SIZE)
        ).all()
    )
    for action in actions:
        db.delete(action)
    db.commit()

    root = staged_attachments_root()
    for blob_name in blob_names:
        with contextlib.suppress(OSError):
            (root / blob_name).unlink()
    return len(actions), len(attachments)


def prune_cache_once(*, now: datetime | None = None) -> tuple[int, int]:
    if host_maintenance_active():
        return 0, 0
    current = now or datetime.now(UTC)
    store = get_live_merge_store()
    files_removed = store.prune(now=current.timestamp()) if store is not None else 0
    with SessionLocal() as db:
        unknowns_removed = prune_diagnostic_unknowns(db, now=current)
        actions_removed, attachments_removed = prune_tracker_outbox(db, now=current.timestamp())
    if files_removed or unknowns_removed:
        logger.info(
            "Pruned %s live-merge file(s) and %s diagnostic unknown(s)",
            files_removed,
            unknowns_removed,
        )
    if actions_removed or attachments_removed:
        logger.info(
            "Pruned %s successful Tracker action(s) and %s uploaded staged blob(s)",
            actions_removed,
            attachments_removed,
        )
    return files_removed, unknowns_removed


async def run_cache_cleanup_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 3600.0,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(prune_cache_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cache cleanup cycle failed")

        if stop_event.is_set():
            break
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
