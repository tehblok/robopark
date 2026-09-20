"""Lease-owned cleanup for bounded shared cache state."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import SessionLocal
from robopark_api.services import emergency_cache, tracker_cache
from robopark_api.services.diagnostic_unknowns import prune_diagnostic_unknowns
from robopark_api.services.live_merge import get_live_merge_store
from robopark_api.services.ops.maintenance import host_maintenance_active
from robopark_api.services.report_attachments import (
    prune_deleted_report_files,
    reconcile_pending_report_deletions,
)
from robopark_api.services.storage_retention import MemoryPressureController
from robopark_api.services.task_timeline import staged_attachments_root
from robopark_api.task_workflow_models import ReliableAction, TaskAttachment

logger = logging.getLogger(__name__)

_RETENTION_BATCH_SIZE = 500
_SUCCESS_RETENTION_SECONDS = 30 * 86400
_UPLOADED_BLOB_RETENTION_SECONDS = 7 * 86400


def _evict_application_caches() -> None:
    tracker_cache.clear_all()
    emergency_cache.clear_cache()


_memory_pressure = MemoryPressureController(evict=_evict_application_caches)
_last_memory_pressure = {"sustained": False, "evicted": False, "failed": False}


def reset_memory_pressure_controller(*, required_samples: int = 3) -> None:
    global _last_memory_pressure, _memory_pressure
    _memory_pressure = MemoryPressureController(
        required_samples=required_samples, evict=_evict_application_caches
    )
    _last_memory_pressure = {"sustained": False, "evicted": False, "failed": False}


def observe_memory_pressure(utilization: float) -> dict[str, bool]:
    """Evict caches once on sustained pressure; never trigger a restart loop."""
    global _last_memory_pressure
    result = _memory_pressure.observe(utilization)
    _last_memory_pressure = result
    if result["failed"]:
        logger.error("Sustained memory pressure remains after cache eviction")
    return result


def sample_memory_pressure(cgroup: Path = Path("/sys/fs/cgroup")) -> dict[str, bool] | None:
    try:
        maximum = (cgroup / "memory.max").read_text().strip()
        used = int((cgroup / "memory.current").read_text().strip())
        if maximum == "max":
            return None
        limit = int(maximum)
        if limit <= 0:
            return None
    except (OSError, ValueError):
        return None
    return observe_memory_pressure(max(0.0, used / limit))


def memory_pressure_status() -> dict[str, bool]:
    return dict(_last_memory_pressure)


def prune_tracker_outbox(db: Session, *, now: float) -> tuple[int, int]:
    """Bound reliable-action rows and uploaded staging blobs without losing audit."""
    attachments = list(
        db.scalars(
            select(TaskAttachment)
            .join(ReliableAction, ReliableAction.id == TaskAttachment.id)
            .where(
                TaskAttachment.uploaded_at.is_not(None),
                TaskAttachment.uploaded_at < now - _UPLOADED_BLOB_RETENTION_SECONDS,
                ReliableAction.state == "succeeded",
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
        pending_reports_removed = reconcile_pending_report_deletions(db)
    deleted_report_files = prune_deleted_report_files(now=current.timestamp())
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
    if deleted_report_files:
        logger.info("Pruned %s deleted-report quarantine file(s)", deleted_report_files)
    if pending_reports_removed:
        logger.info("Completed %s pending report deletion(s)", pending_reports_removed)
    return files_removed, unknowns_removed


async def run_cache_cleanup_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 3600.0,
    pressure_interval_seconds: float = 30.0,
) -> None:
    loop = asyncio.get_running_loop()
    next_cleanup = 0.0
    while not stop_event.is_set():
        try:
            now = loop.time()
            if now >= next_cleanup:
                await asyncio.to_thread(prune_cache_once)
                next_cleanup = now + interval_seconds
            await asyncio.to_thread(sample_memory_pressure)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cache cleanup cycle failed")

        if stop_event.is_set():
            break
        timeout = min(pressure_interval_seconds, max(0.01, next_cleanup - loop.time()))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
