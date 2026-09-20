"""Lease-owned cleanup for bounded shared cache state."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import stat
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.db import SessionLocal
from robopark_api.services import emergency_cache, tracker_cache
from robopark_api.services.diagnostic_unknowns import prune_diagnostic_unknowns
from robopark_api.services.live_merge import get_live_merge_store
from robopark_api.services.ops.context import resolved_ops_dir
from robopark_api.services.ops.maintenance import host_maintenance_active
from robopark_api.services.report_attachments import (
    prune_deleted_report_files,
    reconcile_pending_report_deletions,
)
from robopark_api.services.storage_retention import (
    CleanupRetry,
    MemoryPressureController,
    StorageBudget,
    cleanup_storage,
    pinned_directory,
    unlink_unchanged,
)
from robopark_api.services.task_timeline import staged_attachments_root
from robopark_api.task_workflow_models import ReliableAction, TaskAttachment

logger = logging.getLogger(__name__)

_RETENTION_BATCH_SIZE = 500
_SUCCESS_RETENTION_SECONDS = 30 * 86400
_UPLOADED_BLOB_RETENTION_SECONDS = 7 * 86400
_STORAGE_BATCH_SIZE = 128
_STORAGE_MAX_DELETIONS = 512
_STORAGE_MAX_ITERATIONS = 16
_STORAGE_TIME_BUDGET_SECONDS = 0.5


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
    root = staged_attachments_root()
    pinned_blobs: list[tuple[str, os.stat_result]] = []
    manager = pinned_directory(root) if root.exists() else contextlib.nullcontext(None)
    with manager as root_fd:
        for attachment in attachments:
            name = attachment.blob_name
            if name != Path(name).name:
                continue
            try:
                info = (
                    os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                    if root_fd is not None
                    else None
                )
            except OSError:
                info = None
            if info is not None and stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                pinned_blobs.append((name, info))
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
        for blob_name, before in pinned_blobs:
            with contextlib.suppress(OSError):
                assert root_fd is not None
                unlink_unchanged(root_fd, blob_name, before)
    return len(actions), len(attachments)


def cleanup_confirmed_tracker_copies(
    db: Session, *, budget: StorageBudget, max_deletions: int = _STORAGE_BATCH_SIZE
) -> dict:
    """Delete only local copies already confirmed by a succeeded delivery."""
    attachments = list(
        db.scalars(
            select(TaskAttachment)
            .join(ReliableAction, ReliableAction.id == TaskAttachment.id)
            .where(
                TaskAttachment.uploaded_at.is_not(None),
                ReliableAction.state == "succeeded",
            )
            .order_by(TaskAttachment.uploaded_at, TaskAttachment.id)
            .limit(max(0, max_deletions))
        ).all()
    )
    names = {attachment.blob_name for attachment in attachments}
    report = cleanup_storage(
        roots={"confirmed_tracker": staged_attachments_root()},
        budget=budget,
        dry_run=False,
        max_deletions=max_deletions,
        eligible_names={"confirmed_tracker": names},
    )
    retired = {item["path"] for item in report["deleted"]}
    retired.update(report["eligible_missing"].get("confirmed_tracker", []))
    for attachment in attachments:
        if attachment.blob_name in retired:
            db.delete(attachment)
    db.commit()
    return report


def _write_pressure_report(report: dict) -> None:
    target = resolved_ops_dir(get_settings()) / "api-storage-retention.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, prefix=".retention-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(temporary_name).unlink()


def cleanup_storage_pressure(*, now: float | None = None) -> dict:
    """Coordinate API-owned pressure cleanup against the real data mount."""
    settings = get_settings()
    data_path = Path(settings.host_data_path)
    current = StorageBudget.for_path(data_path)
    initial = current
    owners = {
        "cache_tmp": {"deleted_count": 0, "batches": 0},
        "diagnostics_logs": {"deleted_count": 0, "batches": 0},
        "confirmed_tracker": {"deleted_count": 0, "batches": 0},
    }
    stamp = time.time() if now is None else now
    store = get_live_merge_store()
    started = time.monotonic()
    deadline = started + _STORAGE_TIME_BUDGET_SECONDS
    iterations = 0
    deleted_count = 0
    stop_reason: str | None = None

    def cache_batch(budget: StorageBudget, limit: int) -> int:
        if store is None:
            return 0
        return store.prune(
            now=stamp,
            blob_max_age_seconds=0,
            max_deletions=limit,
            deadline_monotonic=deadline,
        )

    def diagnostic_batch(budget: StorageBudget, limit: int) -> int:
        return prune_deleted_report_files(
            now=stamp,
            max_age_seconds=0,
            max_deletions=limit,
            deadline_monotonic=deadline,
        )

    def tracker_batch(budget: StorageBudget, limit: int) -> int:
        with SessionLocal() as db:
            return cleanup_confirmed_tracker_copies(db, budget=budget, max_deletions=limit)[
                "deleted_count"
            ]

    for name, owner in (
        ("cache_tmp", cache_batch),
        ("diagnostics_logs", diagnostic_batch),
        ("confirmed_tracker", tracker_batch),
    ):
        while current.bytes_to_reclaim:
            if iterations >= _STORAGE_MAX_ITERATIONS:
                stop_reason = "iteration_budget"
                break
            if deleted_count >= _STORAGE_MAX_DELETIONS:
                stop_reason = "deletion_budget"
                break
            if time.monotonic() >= deadline:
                stop_reason = "time_budget"
                break
            limit = min(_STORAGE_BATCH_SIZE, _STORAGE_MAX_DELETIONS - deleted_count)
            removed = owner(current, limit)
            iterations += 1
            deleted_count += removed
            owners[name]["batches"] += 1
            owners[name]["deleted_count"] += removed
            if removed == 0:
                break
            current = StorageBudget.for_path(data_path)
        if stop_reason is not None:
            break

    if stop_reason is None:
        stop_reason = "floor_reached" if not current.bytes_to_reclaim else "no_eligible_data"

    report = {
        "floor_bytes": initial.floor_bytes,
        "bytes_to_reclaim": initial.bytes_to_reclaim,
        "free_bytes": current.free_bytes,
        "pressure": current.bytes_to_reclaim > 0,
        "owners": owners,
        "iterations": iterations,
        "deleted_count": deleted_count,
        "partial": current.bytes_to_reclaim > 0
        and stop_reason in {"iteration_budget", "deletion_budget", "time_budget"},
        "stop_reason": stop_reason,
        "completed_at": stamp,
    }
    _write_pressure_report(report)
    return report


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
    cleanup_storage_pressure(now=current.timestamp())
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
    retry = CleanupRetry(minimum=max(30.0, pressure_interval_seconds))
    while not stop_event.is_set():
        now = loop.time()
        if now >= next_cleanup:
            try:
                await asyncio.to_thread(prune_cache_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                next_cleanup = now + retry.failed()
                if retry.should_log(now):
                    logger.exception("Cache cleanup cycle failed; retry is bounded")
            else:
                retry.succeeded()
                next_cleanup = now + interval_seconds
        try:
            await asyncio.to_thread(sample_memory_pressure)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Memory pressure sampling failed")

        if stop_event.is_set():
            break
        timeout = max(1.0, pressure_interval_seconds)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
