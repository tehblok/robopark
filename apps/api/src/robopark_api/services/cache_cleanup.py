"""Lease-owned cleanup for bounded shared cache state."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import stat
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, delete, exists, or_, select
from sqlalchemy.orm import Session

from robopark_api.collaboration_models import TrackerClaim
from robopark_api.config import get_settings
from robopark_api.db import SessionLocal
from robopark_api.models import AuthThrottleState
from robopark_api.routers import push
from robopark_api.schedule_models import SystemIncidentOccurrence
from robopark_api.services import (
    emergency_cache,
    inventory_photo_cleanup,
    media_uploads,
    schedules,
    tracker_cache,
)
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
    confirmed_staged_attachment,
    pinned_directory,
    unlink_unchanged,
)
from robopark_api.services.system_observability import prune_observability
from robopark_api.services.task_timeline import staged_attachments_root
from robopark_api.task_workflow_models import (
    OfflineSyncReceipt,
    ReliableAction,
    TaskAttachment,
    TaskMessage,
)

logger = logging.getLogger(__name__)

_RETENTION_BATCH_SIZE = 500
_SUCCESS_RETENTION_SECONDS = 30 * 86400
_SYNC_RECEIPT_RETENTION_SECONDS = 30 * 86400
_UPLOADED_BLOB_RETENTION_SECONDS = 7 * 86400
_SYSTEM_INCIDENT_RETENTION_SECONDS = 90 * 86400
_STORAGE_BATCH_SIZE = 128
_STORAGE_MAX_DELETIONS = 512
_STORAGE_MAX_ITERATIONS = 16
_STORAGE_MAX_SCANNED_ENTRIES = 4096
_STORAGE_TIME_BUDGET_SECONDS = 0.5
_ATTACHMENT_SCAN_LIMIT = 4096
_attachment_scan_cursors: dict[str, tuple[float, str]] = {}
_attachment_scan_lock = threading.Lock()


def _confirmed_attachment_rows(
    db: Session, *, cutoff: float, limit: int, owner: str
) -> list[tuple[TaskAttachment, ReliableAction, TaskMessage]]:
    """Keyset-scan a bounded window; remember progress past protected rows."""
    selected = []
    scanned = 0
    cursor_key = f"{owner}:{db.get_bind().url}"
    with _attachment_scan_lock:
        cursor = _attachment_scan_cursors.get(cursor_key)
        while len(selected) < limit and scanned < _ATTACHMENT_SCAN_LIMIT:
            query = (
                select(TaskAttachment, ReliableAction, TaskMessage)
                .join(ReliableAction, ReliableAction.id == TaskAttachment.id)
                .join(TaskMessage, TaskMessage.id == TaskAttachment.message_id)
                .where(
                    TaskAttachment.uploaded_at.is_not(None),
                    TaskAttachment.uploaded_at < cutoff,
                    ReliableAction.state == "succeeded",
                    ReliableAction.action == "attach",
                    TaskMessage.sync_state == "synced",
                )
                .order_by(TaskAttachment.uploaded_at, TaskAttachment.id)
                .limit(min(_RETENTION_BATCH_SIZE, _ATTACHMENT_SCAN_LIMIT - scanned))
            )
            if cursor is not None:
                query = query.where(
                    or_(
                        TaskAttachment.uploaded_at > cursor[0],
                        and_(
                            TaskAttachment.uploaded_at == cursor[0],
                            TaskAttachment.id > cursor[1],
                        ),
                    )
                )
            rows = list(db.execute(query).all())
            if not rows:
                _attachment_scan_cursors.pop(cursor_key, None)
                break
            for row in rows:
                scanned += 1
                cursor = (row[0].uploaded_at, row[0].id)
                _attachment_scan_cursors[cursor_key] = cursor
                if confirmed_staged_attachment(
                    state=row[1].state,
                    action=row[1].action,
                    sync_state=row[2].sync_state,
                    result_json=row[1].result_json,
                    uploaded_at=row[0].uploaded_at,
                ):
                    selected.append(row)
                    if len(selected) >= limit:
                        break
    return selected


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
    attachments = _confirmed_attachment_rows(
        db,
        cutoff=now - _UPLOADED_BLOB_RETENTION_SECONDS,
        limit=_RETENTION_BATCH_SIZE,
        owner="normal",
    )
    root = staged_attachments_root()
    pinned_blobs: list[tuple[str, os.stat_result]] = []
    manager = pinned_directory(root) if root.exists() else contextlib.nullcontext(None)
    with manager as root_fd:
        confirmed = [attachment for attachment, _, _ in attachments]
        for attachment in confirmed:
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
                    ~exists(
                        select(TaskAttachment.id).where(TaskAttachment.id == ReliableAction.id)
                    ),
                    ~exists(
                        select(TaskMessage.id).where(
                            TaskMessage.action_id == ReliableAction.id,
                            TaskMessage.sync_state != "synced",
                        )
                    ),
                    ~exists(
                        select(TrackerClaim.issue_key).where(
                            TrackerClaim.start_action_id == ReliableAction.id
                        )
                    ),
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
    return len(actions), len(confirmed)


def prune_offline_sync_receipts(db: Session, *, now: float) -> int:
    rows = list(
        db.scalars(
            select(OfflineSyncReceipt)
            .where(OfflineSyncReceipt.created_at < now - _SYNC_RECEIPT_RETENTION_SECONDS)
            .order_by(OfflineSyncReceipt.created_at, OfflineSyncReceipt.id)
            .limit(_RETENTION_BATCH_SIZE)
        ).all()
    )
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)


def prune_auth_throttle_states(
    db: Session,
    *,
    now: datetime,
    limit: int = _RETENTION_BATCH_SIZE,
) -> int:
    """Delete at most one bounded batch of expired shared throttle rows."""
    key_hashes = list(
        db.scalars(
            select(AuthThrottleState.key_hash)
            .where(AuthThrottleState.expires_at <= now)
            .order_by(AuthThrottleState.expires_at, AuthThrottleState.key_hash)
            .limit(max(0, limit))
        ).all()
    )
    removed = _delete_expired_auth_throttle_keys(db, key_hashes=key_hashes, cutoff=now)
    if key_hashes:
        db.commit()
    return removed


def prune_system_incident_occurrences(
    db: Session,
    *,
    now: datetime,
    limit: int = _RETENTION_BATCH_SIZE,
) -> int:
    """Delete one bounded batch of old resolved incident occurrences."""
    cutoff = now - timedelta(seconds=_SYSTEM_INCIDENT_RETENTION_SECONDS)
    occurrence_ids = list(
        db.scalars(
            select(SystemIncidentOccurrence.id)
            .where(
                SystemIncidentOccurrence.resolved_at.is_not(None),
                SystemIncidentOccurrence.resolved_at <= cutoff,
            )
            .order_by(SystemIncidentOccurrence.resolved_at, SystemIncidentOccurrence.id)
            .limit(max(0, limit))
        ).all()
    )
    if not occurrence_ids:
        return 0
    result = db.execute(
        delete(SystemIncidentOccurrence)
        .where(
            SystemIncidentOccurrence.id.in_(occurrence_ids),
            SystemIncidentOccurrence.resolved_at.is_not(None),
            SystemIncidentOccurrence.resolved_at <= cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return int(result.rowcount or 0)


def _delete_expired_auth_throttle_keys(
    db: Session, *, key_hashes: list[str], cutoff: datetime
) -> int:
    """Delete selected rows only when they are still expired at write time."""
    if not key_hashes:
        return 0
    result = db.execute(
        delete(AuthThrottleState)
        .where(
            AuthThrottleState.key_hash.in_(key_hashes),
            AuthThrottleState.expires_at <= cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def cleanup_confirmed_tracker_copies(
    db: Session,
    *,
    budget: StorageBudget,
    max_deletions: int = _STORAGE_BATCH_SIZE,
    max_scanned_entries: int | None = None,
    deadline_monotonic: float | None = None,
) -> dict:
    """Delete only local copies already confirmed by a succeeded delivery."""
    attachments = [
        attachment
        for attachment, _, _ in _confirmed_attachment_rows(
            db,
            cutoff=time.time() - _UPLOADED_BLOB_RETENTION_SECONDS,
            limit=max(0, max_deletions),
            owner="pressure",
        )
    ]
    names = {attachment.blob_name for attachment in attachments}
    report = cleanup_storage(
        roots={"confirmed_tracker": staged_attachments_root()},
        budget=budget,
        dry_run=False,
        max_deletions=max_deletions,
        eligible_names={"confirmed_tracker": names},
        max_scanned_entries=max_scanned_entries,
        deadline_monotonic=deadline_monotonic,
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
        "cache_tmp": {"deleted_count": 0, "batches": 0, "scanned_count": 0},
        "diagnostics_logs": {"deleted_count": 0, "batches": 0, "scanned_count": 0},
        "confirmed_tracker": {"deleted_count": 0, "batches": 0, "scanned_count": 0},
    }
    stamp = time.time() if now is None else now
    store = get_live_merge_store()
    started = time.monotonic()
    deadline = started + _STORAGE_TIME_BUDGET_SECONDS
    iterations = 0
    deleted_count = 0
    scanned_count = 0
    stop_reason: str | None = None

    def cache_batch(budget: StorageBudget, limit: int, scan_limit: int) -> dict:
        if store is None:
            return {"deleted_count": 0}
        return {
            "deleted_count": store.prune(
                now=stamp,
                blob_max_age_seconds=0,
                max_deletions=limit,
                deadline_monotonic=deadline,
            )
        }

    def diagnostic_batch(budget: StorageBudget, limit: int, scan_limit: int) -> dict:
        return {
            "deleted_count": prune_deleted_report_files(
                now=stamp,
                max_age_seconds=0,
                max_deletions=limit,
                deadline_monotonic=deadline,
            )
        }

    def tracker_batch(budget: StorageBudget, limit: int, scan_limit: int) -> dict:
        with SessionLocal() as db:
            return cleanup_confirmed_tracker_copies(
                db,
                budget=budget,
                max_deletions=limit,
                max_scanned_entries=scan_limit,
                deadline_monotonic=deadline,
            )

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
            if scanned_count >= _STORAGE_MAX_SCANNED_ENTRIES:
                stop_reason = "scan_budget"
                break
            if time.monotonic() >= deadline:
                stop_reason = "time_budget"
                break
            limit = min(_STORAGE_BATCH_SIZE, _STORAGE_MAX_DELETIONS - deleted_count)
            scan_limit = _STORAGE_MAX_SCANNED_ENTRIES - scanned_count
            outcome = owner(current, limit, scan_limit)
            removed = int(outcome.get("deleted_count", 0))
            scanned = int(outcome.get("scanned_count", 0))
            iterations += 1
            deleted_count += removed
            scanned_count += scanned
            owners[name]["batches"] += 1
            owners[name]["deleted_count"] += removed
            owners[name]["scanned_count"] += scanned
            if outcome.get("partial") is True:
                stop_reason = str(outcome.get("stop_reason") or "owner_budget")
                break
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
        "scanned_count": scanned_count,
        "partial": current.bytes_to_reclaim > 0
        and stop_reason
        in {
            "iteration_budget",
            "deletion_budget",
            "scan_budget",
            "time_budget",
            "owner_budget",
            "scan_failed",
            "delete_failed",
        },
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
        media_uploads_removed = media_uploads.cleanup_expired(db, now=current.timestamp())
        sync_receipts_removed = prune_offline_sync_receipts(db, now=current.timestamp())
        throttle_states_removed = prune_auth_throttle_states(db, now=current)
        incident_occurrences_removed = prune_system_incident_occurrences(db, now=current)
        prune_observability(db, now=current)
        notification_cleanup = push.prune_notification_data(db, now=current)
        schedules_removed = schedules.prune_old_entries(db, now=current)
        actions_removed, attachments_removed = prune_tracker_outbox(db, now=current.timestamp())
        pending_reports_removed = reconcile_pending_report_deletions(db)
        inventory_photos_removed = inventory_photo_cleanup.process_pending(db)
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
    if media_uploads_removed:
        logger.info("Pruned %s expired media upload(s)", media_uploads_removed)
    if sync_receipts_removed:
        logger.info("Pruned %s expired offline sync receipt(s)", sync_receipts_removed)
    if throttle_states_removed:
        logger.info("Pruned %s expired authentication throttle row(s)", throttle_states_removed)
    if incident_occurrences_removed:
        logger.info(
            "Pruned %s resolved system incident occurrence(s)", incident_occurrences_removed
        )
    if notification_cleanup["subscriptions"] or notification_cleanup["notifications"]:
        logger.info("Pruned notification data: %s", notification_cleanup)
    if schedules_removed:
        logger.info("Pruned %s old schedule entrie(s)", schedules_removed)
    if deleted_report_files:
        logger.info("Pruned %s deleted-report quarantine file(s)", deleted_report_files)
    if pending_reports_removed:
        logger.info("Completed %s pending report deletion(s)", pending_reports_removed)
    if inventory_photos_removed:
        logger.info("Completed %s pending inventory photo cleanup(s)", inventory_photos_removed)
    return files_removed, unknowns_removed


async def run_cache_cleanup_loop(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 3600.0,
    pressure_interval_seconds: float = 30.0,
) -> None:
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cache-cleanup")
    next_cleanup = 0.0
    retry = CleanupRetry(minimum=max(30.0, pressure_interval_seconds))
    try:
        while not stop_event.is_set():
            now = loop.time()
            if now >= next_cleanup:
                try:
                    await loop.run_in_executor(executor, prune_cache_once)
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
                await loop.run_in_executor(executor, sample_memory_pressure)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Memory pressure sampling failed")

            if stop_event.is_set():
                break
            timeout = max(1.0, pressure_interval_seconds)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    finally:
        # asyncio cancellation cannot stop a running worker. Joining this owned
        # executor keeps DB/file writes inside the singleton lease lifetime.
        executor.shutdown(wait=True, cancel_futures=True)
