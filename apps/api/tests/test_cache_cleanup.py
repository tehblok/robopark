import asyncio
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import AuthThrottleState
from robopark_api.routers.push import PushService
from robopark_api.schedule_models import SystemIncidentOccurrence
from robopark_api.services import cache_cleanup, worker_runtime
from robopark_api.services.ops.ota_uploads import OtaUploadStore
from robopark_api.task_workflow_models import (
    OfflineSyncReceipt,
    ReliableAction,
    TaskAttachment,
    TaskMessage,
)


def test_hourly_cleanup_reclaims_only_expired_ota_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "robopark_api.services.ops.ota_uploads.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100 * 1024**3, free=80 * 1024**3),
    )
    state = tmp_path / "ops" / "ota-uploads"
    host = tmp_path / "host" / "ota-uploads"
    store = OtaUploadStore(state, host)
    old = store.create(actor_id=7, filename="old.ota", size=10, sha256="a" * 64)
    current = store.create(actor_id=7, filename="current.ota", size=10, sha256="b" * 64)
    metadata = store._read(old.upload_id)
    metadata["expires_at"] = 0
    store._write(old.upload_id, metadata)
    settings = SimpleNamespace(
        ops_dir=str(tmp_path / "ops"),
        ops_host_root=str(tmp_path / "host"),
        ops_max_upload_bytes=100,
    )
    monkeypatch.setattr(cache_cleanup, "get_settings", lambda: settings)

    assert cache_cleanup.prune_expired_ota_uploads() == 1
    assert not store._meta(old.upload_id).exists()
    assert store._part(current.upload_id).exists()


def test_hourly_cleanup_keeps_pinned_ota_until_durable_terminal_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "robopark_api.services.ops.ota_uploads.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100 * 1024**3, free=80 * 1024**3),
    )
    state = tmp_path / "ops" / "ota-uploads"
    host = tmp_path / "host" / "ota-uploads"
    store = OtaUploadStore(state, host)
    upload = store.create(actor_id=7, filename="release.ota", size=10, sha256="a" * 64)
    metadata = store._read(upload.upload_id)
    metadata.update(state="verified", offset=10)
    store._write(upload.upload_id, metadata)
    store._host(upload.upload_id).write_bytes(b"0123456789")
    operation_id = uuid4()
    store.pin_for_operation(upload.upload_id, actor_id=7, operation_id=operation_id)
    metadata = store._read(upload.upload_id)
    metadata["expires_at"] = 0
    store._write(upload.upload_id, metadata)
    settings = SimpleNamespace(
        ops_dir=str(tmp_path / "ops"),
        ops_host_root=str(tmp_path / "host"),
        ops_max_upload_bytes=100,
    )
    monkeypatch.setattr(cache_cleanup, "get_settings", lambda: settings)
    monkeypatch.setattr(
        cache_cleanup.operation_registry, "snapshot_current_job", lambda *_args: None
    )
    receipt = SimpleNamespace(receipt_state="accepted", error=None)

    class Registry:
        def get(self, _model, identity):
            return receipt if identity == str(operation_id) else None

    db = Registry()
    assert cache_cleanup.prune_expired_ota_uploads(db) == 0
    assert store._host(upload.upload_id).exists()
    receipt.receipt_state = "terminal"
    receipt.error = "ota_rollback_failed"
    assert cache_cleanup.prune_expired_ota_uploads(db) == 0
    receipt.error = None
    assert cache_cleanup.prune_expired_ota_uploads(db) == 1
    assert not store._host(upload.upload_id).exists()


def _patch_idle_worker_loops(monkeypatch, *, except_names=frozenset()):
    async def idle(*args, **kwargs):
        stop = next(arg for arg in args if isinstance(arg, asyncio.Event))
        await stop.wait()

    for name in (
        "run_keepalive_loop",
        "run_blocker_history_loop",
        "run_session_cleanup_loop",
        "run_cache_cleanup_loop",
        "run_system_notification_loop",
        "run_tracker_outbox_loop",
        "run_campaign_refresh_loop",
        "run_tracker_notification_loop",
    ):
        if name not in except_names:
            monkeypatch.setattr(worker_runtime, name, idle)


def test_cleanup_once_prunes_live_merge_and_diagnostic_unknowns(monkeypatch):
    now = datetime(2026, 9, 14, tzinfo=UTC)
    db = object()
    calls = []

    class SessionContext:
        def __enter__(self):
            return db

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    class Store:
        def prune(self, **kwargs):
            calls.append(("files", kwargs))
            return 4

    monkeypatch.setattr(cache_cleanup, "SessionLocal", SessionContext)
    monkeypatch.setattr(cache_cleanup, "get_live_merge_store", lambda: Store())
    monkeypatch.setattr(
        cache_cleanup,
        "prune_diagnostic_unknowns",
        lambda session, **kwargs: calls.append(("unknowns", session, kwargs)) or 3,
    )
    monkeypatch.setattr(
        cache_cleanup,
        "prune_tracker_outbox",
        lambda session, **kwargs: calls.append(("outbox", session, kwargs)) or (0, 0),
    )
    monkeypatch.setattr(
        cache_cleanup,
        "prune_offline_sync_receipts",
        lambda session, **kwargs: calls.append(("receipts", session, kwargs)) or 1,
    )
    monkeypatch.setattr(
        cache_cleanup,
        "prune_auth_throttle_states",
        lambda session, **kwargs: calls.append(("throttles", session, kwargs)) or 2,
    )
    monkeypatch.setattr(
        cache_cleanup,
        "prune_system_incident_occurrences",
        lambda session, **kwargs: calls.append(("incidents", session, kwargs)) or 2,
    )
    monkeypatch.setattr(
        cache_cleanup,
        "prune_operation_registry",
        lambda session, **kwargs: calls.append(("operation-receipts", session, kwargs)) or 5,
    )
    monkeypatch.setattr(
        cache_cleanup,
        "prune_observability",
        lambda session, **kwargs: calls.append(("observability", session, kwargs)) or (0, 0),
    )
    monkeypatch.setattr(
        cache_cleanup,
        "prune_notification_backlog",
        lambda session, **kwargs: (
            calls.append(("notifications", session, kwargs))
            or {"subscriptions": 1, "notifications": 2, "batches": 1, "pending": False}
        ),
    )
    monkeypatch.setattr(
        cache_cleanup.schedules,
        "prune_old_entries",
        lambda session, **kwargs: calls.append(("schedules", session, kwargs)) or 2,
    )
    monkeypatch.setattr(
        cache_cleanup.media_uploads,
        "cleanup_expired",
        lambda session, **kwargs: calls.append(("media", session, kwargs)) or 2,
    )
    monkeypatch.setattr(
        cache_cleanup,
        "prune_deleted_report_files",
        lambda **kwargs: calls.append(("report-files", kwargs)) or 0,
    )
    monkeypatch.setattr(
        cache_cleanup,
        "reconcile_pending_report_deletions",
        lambda session: calls.append(("pending-reports", session)) or 0,
    )
    monkeypatch.setattr(
        cache_cleanup.inventory_photo_cleanup,
        "process_pending",
        lambda session: calls.append(("inventory-photos", session)) or 0,
    )
    monkeypatch.setattr(
        cache_cleanup,
        "cleanup_storage_pressure",
        lambda **kwargs: calls.append(("pressure", kwargs)) or {},
    )

    assert cache_cleanup.prune_cache_once(now=now) == (4, 3)
    assert calls == [
        ("files", {"now": now.timestamp()}),
        ("unknowns", db, {"now": now}),
        ("media", db, {"now": now.timestamp()}),
        ("receipts", db, {"now": now.timestamp()}),
        ("throttles", db, {"now": now}),
        ("incidents", db, {"now": now}),
        ("operation-receipts", db, {"now": now}),
        ("observability", db, {"now": now}),
        ("notifications", db, {"now": now}),
        ("schedules", db, {"now": now}),
        ("outbox", db, {"now": now.timestamp()}),
        ("pending-reports", db),
        ("inventory-photos", db),
        ("report-files", {"now": now.timestamp()}),
        ("pressure", {"now": now.timestamp()}),
    ]


def test_cleanup_bounds_confirmed_offline_sync_receipts(db_session, seed_mechanic):
    now = datetime(2026, 9, 20, tzinfo=UTC).timestamp()
    old = OfflineSyncReceipt(
        actor_user_id=seed_mechanic.id,
        device_id="old-device",
        client_action_id="old-action",
        payload_hash="a" * 64,
        result_json="{}",
        created_at=now - 31 * 86400,
    )
    recent = OfflineSyncReceipt(
        actor_user_id=seed_mechanic.id,
        device_id="new-device",
        client_action_id="new-action",
        payload_hash="b" * 64,
        result_json="{}",
        created_at=now - 86400,
    )
    db_session.add_all([old, recent])
    db_session.commit()

    assert cache_cleanup.prune_offline_sync_receipts(db_session, now=now) == 1
    assert db_session.get(OfflineSyncReceipt, old.id) is None
    assert db_session.get(OfflineSyncReceipt, recent.id) is not None


def test_cleanup_bounds_expired_auth_throttle_rows(db_session):
    now = datetime(2026, 9, 20, tzinfo=UTC)
    for index in range(3):
        db_session.add(
            AuthThrottleState(
                key_hash=f"{index:064x}",
                failure_count=1,
                window_started_at=now - timedelta(minutes=2),
                expires_at=now - timedelta(minutes=1),
            )
        )
    db_session.commit()

    assert cache_cleanup.prune_auth_throttle_states(db_session, now=now, limit=2) == 2
    assert db_session.scalar(select(func.count()).select_from(AuthThrottleState)) == 1


def test_cleanup_does_not_delete_auth_throttle_row_renewed_after_selection(db_session, db_engine):
    cutoff = datetime(2026, 9, 20, tzinfo=UTC)
    key_hash = "a" * 64
    db_session.add(
        AuthThrottleState(
            key_hash=key_hash,
            failure_count=1,
            window_started_at=cutoff - timedelta(minutes=2),
            expires_at=cutoff - timedelta(minutes=1),
        )
    )
    db_session.commit()

    # Cleanup selected this key while it was stale. An auth worker renews it
    # before the DELETE reaches the database.
    with Session(db_engine) as writer:
        row = writer.get(AuthThrottleState, key_hash)
        row.expires_at = cutoff + timedelta(minutes=5)
        writer.commit()

    assert (
        cache_cleanup._delete_expired_auth_throttle_keys(
            db_session, key_hashes=[key_hash], cutoff=cutoff
        )
        == 0
    )
    db_session.expire_all()
    assert db_session.get(AuthThrottleState, key_hash) is not None


def test_cleanup_bounds_resolved_incidents_and_preserves_active(db_session):
    now = datetime(2026, 9, 20, tzinfo=UTC)
    old = now - timedelta(days=91)
    rows = [
        SystemIncidentOccurrence(
            incident_key=f"system:old:{index}",
            event_type="problem",
            started_at=old - timedelta(minutes=1),
            last_seen_at=old,
            resolved_at=old,
        )
        for index in range(3)
    ]
    active = SystemIncidentOccurrence(
        incident_key="system:active",
        event_type="problem",
        started_at=old,
        last_seen_at=old,
        resolved_at=None,
    )
    recent = SystemIncidentOccurrence(
        incident_key="system:recent",
        event_type="problem",
        started_at=now - timedelta(days=2),
        last_seen_at=now - timedelta(days=1),
        resolved_at=now - timedelta(days=1),
    )
    db_session.add_all([*rows, active, recent])
    db_session.commit()
    old_ids = [row.id for row in rows]
    active_id = active.id
    recent_id = recent.id

    assert cache_cleanup.prune_system_incident_occurrences(db_session, now=now, limit=2) == 2
    assert db_session.get(SystemIncidentOccurrence, active_id) is not None
    assert db_session.get(SystemIncidentOccurrence, recent_id) is not None
    assert (
        sum(
            db_session.get(SystemIncidentOccurrence, occurrence_id) is not None
            for occurrence_id in old_ids
        )
        == 1
    )


def test_deleted_report_file_cleanup_only_removes_old_quarantine_files(tmp_path, monkeypatch):
    from robopark_api.services import report_attachments

    monkeypatch.setattr(report_attachments, "attachments_root", lambda: tmp_path)
    stage = tmp_path / ".delete-staging"
    stage.mkdir()
    old = stage / "old-file"
    fresh = stage / "new-file"
    old.write_bytes(b"old")
    fresh.write_bytes(b"new")
    import os

    os.utime(old, (1000, 1000))
    os.utime(fresh, (5000, 5000))
    assert report_attachments.prune_deleted_report_files(now=5000, max_age_seconds=3600) == 1
    assert not old.exists()
    assert fresh.read_bytes() == b"new"


def test_cleanup_loop_runs_without_sleeping_and_stops_on_event(monkeypatch):
    called = threading.Event()

    def fake_cleanup_once():
        called.set()
        return (0, 0)

    monkeypatch.setattr(cache_cleanup, "prune_cache_once", fake_cleanup_once)

    async def exercise():
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            cache_cleanup.run_cache_cleanup_loop(stop_event, interval_seconds=3600)
        )
        assert await asyncio.to_thread(called.wait, 1)
        stop_event.set()
        await task

    asyncio.run(exercise())


def test_cleanup_loop_continues_notification_backlog_on_the_next_worker_tick(monkeypatch):
    calls = 0

    async def no_wait(awaitable, *, timeout):
        awaitable.close()
        raise TimeoutError

    async def exercise():
        nonlocal calls
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def fake_cleanup_once():
            nonlocal calls
            calls += 1
            cache_cleanup._notification_cleanup_pending = calls == 1
            if calls == 2:
                loop.call_soon_threadsafe(stop_event.set)
            return (0, 0)

        monkeypatch.setattr(cache_cleanup, "prune_cache_once", fake_cleanup_once)
        monkeypatch.setattr(cache_cleanup, "sample_memory_pressure", lambda: None)
        monkeypatch.setattr(asyncio, "wait_for", no_wait)
        await cache_cleanup.run_cache_cleanup_loop(
            stop_event,
            interval_seconds=3600,
            pressure_interval_seconds=0,
        )

    asyncio.run(exercise())
    assert calls == 2


def test_notification_backlog_respects_total_deletion_budget(monkeypatch):
    limits = []
    monkeypatch.setattr(
        cache_cleanup.push, "prune_expired_subscriptions", lambda *_args, **_kwargs: 0
    )
    monkeypatch.setattr(
        cache_cleanup.push,
        "prune_expired_notifications_batch",
        lambda _db, *, cutoff, limit: limits.append(limit) or limit,
    )
    monkeypatch.setattr(
        cache_cleanup.push, "has_expired_notifications", lambda *_args, **_kwargs: True
    )

    result = cache_cleanup.prune_notification_backlog(
        object(),
        now=datetime(2026, 10, 8, tzinfo=UTC),
        batch_size=500,
        max_notifications=750,
        time_budget_seconds=1,
        clock=lambda: 0,
    )

    assert limits == [500, 250]
    assert result["notifications"] == 750
    assert result["pending"] is True


def test_notification_backlog_respects_time_budget_between_batches(monkeypatch):
    limits = []
    clock = iter((0.0, 0.0, 1.0)).__next__
    monkeypatch.setattr(
        cache_cleanup.push, "prune_expired_subscriptions", lambda *_args, **_kwargs: 0
    )
    monkeypatch.setattr(
        cache_cleanup.push,
        "prune_expired_notifications_batch",
        lambda _db, *, cutoff, limit: limits.append(limit) or limit,
    )
    monkeypatch.setattr(
        cache_cleanup.push, "has_expired_notifications", lambda *_args, **_kwargs: True
    )

    result = cache_cleanup.prune_notification_backlog(
        object(),
        now=datetime(2026, 10, 8, tzinfo=UTC),
        batch_size=500,
        max_notifications=5_000,
        time_budget_seconds=0.5,
        clock=clock,
    )

    assert limits == [500]
    assert result["notifications"] == 500
    assert result["pending"] is True


def test_cleanup_loop_cancellation_joins_real_worker_thread(monkeypatch):
    started = threading.Event()
    finish = threading.Event()

    def blocking_cleanup():
        started.set()
        finish.wait(timeout=1)
        return (0, 0)

    monkeypatch.setattr(cache_cleanup, "prune_cache_once", blocking_cleanup)

    async def exercise():
        task = asyncio.create_task(cache_cleanup.run_cache_cleanup_loop(asyncio.Event()))
        assert await asyncio.to_thread(started.wait, 0.5)
        threading.Timer(0.1, finish.set).start()
        began = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.monotonic() - began

    assert asyncio.run(exercise()) >= 0.08


def test_cleanup_failure_backs_off_while_pressure_sampling_continues(monkeypatch):
    cleanup_calls = 0
    sample_calls = 0

    def failing_cleanup():
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise RuntimeError("disk temporarily unavailable")

    async def no_wait(awaitable, *, timeout):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(cache_cleanup, "prune_cache_once", failing_cleanup)
    monkeypatch.setattr(asyncio, "wait_for", no_wait)

    async def exercise():
        nonlocal sample_calls
        stop_event = asyncio.Event()

        def sample():
            nonlocal sample_calls
            sample_calls += 1
            if sample_calls == 3:
                stop_event.set()

        monkeypatch.setattr(cache_cleanup, "sample_memory_pressure", sample)
        await cache_cleanup.run_cache_cleanup_loop(
            stop_event, interval_seconds=3600, pressure_interval_seconds=30
        )

    asyncio.run(exercise())
    assert cleanup_calls == 1
    assert sample_calls == 3


@pytest.mark.parametrize(
    ("merge_enabled", "won_lease"),
    [(True, False), (True, True), (False, False), (False, True)],
)
def test_worker_starts_cleanup_only_for_job_lease_owner(
    db_engine, test_settings, monkeypatch, merge_enabled, won_lease
):
    lease_attempted = threading.Event()
    cleanup_started = threading.Event()
    outbox_started = threading.Event()

    class Lease:
        def __init__(self, root, name):
            assert name == "lifespan-jobs"

        def try_acquire(self):
            lease_attempted.set()
            return won_lease

        def release(self):
            return None

    async def idle_loop(stop_event, **kwargs):
        await stop_event.wait()

    async def cleanup_loop(stop_event, **kwargs):
        cleanup_started.set()
        await stop_event.wait()

    async def outbox_loop(session_factory, stop_event, **kwargs):
        assert session_factory is factory
        outbox_started.set()
        await stop_event.wait()

    factory = sessionmaker(bind=db_engine, future=True)
    monkeypatch.setenv("ROBOPARK_LIVE_MERGE", "1" if merge_enabled else "0")
    monkeypatch.setattr(worker_runtime, "host_maintenance_active", lambda _settings: False)
    monkeypatch.setattr(worker_runtime, "JobLease", Lease)
    _patch_idle_worker_loops(
        monkeypatch, except_names={"run_cache_cleanup_loop", "run_tracker_outbox_loop"}
    )
    monkeypatch.setattr(worker_runtime, "run_cache_cleanup_loop", cleanup_loop)
    monkeypatch.setattr(worker_runtime, "run_tracker_outbox_loop", outbox_loop)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(
            worker_runtime.WorkerRuntime(test_settings, factory, PushService(factory)).start(stop)
        )
        assert await asyncio.to_thread(lease_attempted.wait, 1)
        if won_lease:
            assert await asyncio.to_thread(cleanup_started.wait, 1)
            assert await asyncio.to_thread(outbox_started.wait, 1)
        else:
            await asyncio.sleep(0.05)
            assert not cleanup_started.is_set()
            assert not outbox_started.is_set()
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())

    assert cleanup_started.is_set() is won_lease
    assert outbox_started.is_set() is won_lease


def test_cleanup_bounds_successful_actions_and_uploaded_blob_retention(
    db_engine, db_session, seed_mechanic, tmp_path, monkeypatch
):
    cutoff = datetime(2026, 9, 15, tzinfo=UTC).timestamp()
    old = cutoff - 31 * 86400
    recent = cutoff - 1 * 86400
    message = TaskMessage(
        id="retained-message",
        issue_key="ROBOPARK-1",
        kind="system",
        author_name="system",
        text="audit trail",
        sync_state="synced",
        created_at=old,
        updated_at=old,
    )
    db_session.add(message)
    removable = ReliableAction(
        id="old-success",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="attach",
        idempotency_key="old-success-0001",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        result_json='{"attachment_id":"remote-attachment","external_id":"remote-comment"}',
        next_attempt_at=0,
        created_at=old,
        updated_at=old,
    )
    retained_attention = ReliableAction(
        id="old-attention",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="comment",
        idempotency_key="old-attention-0001",
        payload_hash="0" * 64,
        payload_json="{}",
        state="needs_attention",
        next_attempt_at=0,
        created_at=old,
        updated_at=old,
    )
    recent_success = ReliableAction(
        id="recent-success",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="comment",
        idempotency_key="recent-success-0001",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        next_attempt_at=0,
        created_at=recent,
        updated_at=recent,
    )
    db_session.add_all([removable, retained_attention, recent_success])
    db_session.flush()
    message.action_id = removable.id
    blob = tmp_path / "old-blob"
    blob.write_bytes(b"old")
    retained_blob = tmp_path / "attention-blob"
    retained_blob.write_bytes(b"attention")
    db_session.add(
        TaskAttachment(
            id=removable.id,
            message_id=message.id,
            blob_name=blob.name,
            original_name="old.png",
            mime_type="image/png",
            size_bytes=3,
            sha256="0" * 64,
            created_at=old,
            uploaded_at=cutoff - 8 * 86400,
        )
    )
    db_session.add(
        TaskAttachment(
            id=retained_attention.id,
            message_id=message.id,
            blob_name=retained_blob.name,
            original_name="attention.png",
            mime_type="image/png",
            size_bytes=9,
            sha256="1" * 64,
            created_at=old,
            uploaded_at=cutoff - 8 * 86400,
        )
    )
    db_session.commit()
    removable_id = removable.id
    attention_id = retained_attention.id
    recent_id = recent_success.id
    message_id = message.id
    monkeypatch.setattr(cache_cleanup, "staged_attachments_root", lambda: tmp_path)

    with Session(db_engine) as db:
        assert cache_cleanup.prune_tracker_outbox(db, now=cutoff) == (1, 1)

    with Session(db_engine) as db:
        assert db.get(ReliableAction, removable_id) is None
        assert db.get(ReliableAction, attention_id) is not None
        assert db.get(ReliableAction, recent_id) is not None
        assert db.get(TaskMessage, message_id) is not None
        assert db.get(TaskAttachment, removable_id) is None
    assert not blob.exists()
    assert retained_blob.exists()


def test_uploaded_attachment_metadata_survives_transient_unlink_failure(
    db_engine, db_session, seed_mechanic, tmp_path, monkeypatch
):
    cutoff = datetime(2026, 9, 15, tzinfo=UTC).timestamp()
    old = cutoff - 31 * 86400
    message = TaskMessage(
        id="retry-message",
        issue_key="ROBOPARK-1",
        kind="system",
        author_name="system",
        text="audit",
        sync_state="synced",
        created_at=old,
        updated_at=old,
    )
    action = ReliableAction(
        id="retry-attachment",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="attach",
        idempotency_key="retry-attachment-0001",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        result_json='{"attachment_id":"remote","external_id":"comment"}',
        next_attempt_at=0,
        created_at=old,
        updated_at=old,
    )
    db_session.add_all([message, action])
    db_session.flush()
    message.action_id = action.id
    blob = tmp_path / "retry-blob"
    blob.write_bytes(b"old")
    db_session.add(
        TaskAttachment(
            id=action.id,
            message_id=message.id,
            blob_name=blob.name,
            original_name="old.png",
            mime_type="image/png",
            size_bytes=3,
            sha256="0" * 64,
            created_at=old,
            uploaded_at=cutoff - 8 * 86400,
        )
    )
    db_session.commit()
    monkeypatch.setattr(cache_cleanup, "staged_attachments_root", lambda: tmp_path)
    real_unlink = cache_cleanup.unlink_unchanged
    attempts = 0

    def flaky_unlink(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary filesystem error")
        return real_unlink(*args, **kwargs)

    monkeypatch.setattr(cache_cleanup, "unlink_unchanged", flaky_unlink)
    with Session(db_engine) as db:
        assert cache_cleanup.prune_tracker_outbox(db, now=cutoff) == (0, 0)
    with Session(db_engine) as db:
        assert db.get(TaskAttachment, action.id) is not None
    assert blob.exists()

    with Session(db_engine) as db:
        assert cache_cleanup.prune_tracker_outbox(db, now=cutoff) == (1, 1)
    with Session(db_engine) as db:
        assert db.get(TaskAttachment, action.id) is None
    assert not blob.exists()


def test_worker_awaits_blocking_outbox_before_releasing_job_lease(
    db_engine, test_settings, monkeypatch
):
    outbox_started = threading.Event()
    finish_outbox = threading.Event()
    lease_released = threading.Event()

    class Lease:
        def __init__(self, root, name):
            pass

        def try_acquire(self):
            return True

        def release(self):
            lease_released.set()

    async def blocking_outbox(session_factory, stop_event, **kwargs):
        outbox_started.set()
        await asyncio.to_thread(finish_outbox.wait)
        assert stop_event.is_set()

    factory = sessionmaker(bind=db_engine, future=True)
    push_service = PushService(factory)
    push_closed = threading.Event()
    monkeypatch.setattr(push_service, "close", push_closed.set)
    monkeypatch.setattr(worker_runtime, "host_maintenance_active", lambda _settings: False)
    monkeypatch.setattr(worker_runtime, "JobLease", Lease)
    _patch_idle_worker_loops(monkeypatch, except_names={"run_tracker_outbox_loop"})
    monkeypatch.setattr(worker_runtime, "run_tracker_outbox_loop", blocking_outbox)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(
            worker_runtime.WorkerRuntime(test_settings, factory, push_service).start(stop)
        )
        assert await asyncio.to_thread(outbox_started.wait, 1)
        stop.set()
        try:
            await asyncio.sleep(0.05)
            assert not lease_released.is_set()
            assert not push_closed.is_set()
        finally:
            finish_outbox.set()
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert lease_released.is_set()
    assert push_closed.is_set()


def test_worker_joins_real_cleanup_thread_before_closing_shared_resources(
    db_engine, test_settings, monkeypatch
):
    cleanup_started = threading.Event()
    finish_cleanup = threading.Event()
    lease_released = threading.Event()

    class Lease:
        def __init__(self, root, name):
            del root, name

        def try_acquire(self):
            return True

        def release(self):
            lease_released.set()

    def blocking_cleanup():
        cleanup_started.set()
        finish_cleanup.wait(timeout=2)
        return (0, 0)

    factory = sessionmaker(bind=db_engine, future=True)
    push_service = PushService(factory)
    push_closed = threading.Event()
    monkeypatch.setattr(push_service, "close", push_closed.set)
    monkeypatch.setattr(cache_cleanup, "prune_cache_once", blocking_cleanup)
    monkeypatch.setattr(cache_cleanup, "sample_memory_pressure", lambda: None)
    monkeypatch.setattr(worker_runtime, "host_maintenance_active", lambda _settings: False)
    monkeypatch.setattr(worker_runtime, "JobLease", Lease)
    _patch_idle_worker_loops(monkeypatch, except_names={"run_cache_cleanup_loop"})

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(
            worker_runtime.WorkerRuntime(test_settings, factory, push_service).start(stop)
        )
        assert await asyncio.to_thread(cleanup_started.wait, 1)
        stop.set()
        try:
            await asyncio.sleep(0.05)
            assert not lease_released.is_set()
            assert not push_closed.is_set()
        finally:
            finish_cleanup.set()
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert lease_released.is_set()
    assert push_closed.is_set()


def test_cleanup_limits_each_outbox_retention_batch_to_500(db_engine, db_session, seed_mechanic):
    rows = [
        ReliableAction(
            id=f"expired-{index:03d}",
            actor_user_id=seed_mechanic.id,
            resource_type="tracker_issue",
            resource_id=f"ROBOPARK-{index}",
            action="comment",
            idempotency_key=f"expired-action-{index:04d}",
            payload_hash="0" * 64,
            payload_json="{}",
            state="succeeded",
            next_attempt_at=0,
            created_at=1,
            updated_at=1,
        )
        for index in range(501)
    ]
    db_session.add_all(rows)
    db_session.commit()

    with Session(db_engine) as db:
        assert cache_cleanup.prune_tracker_outbox(db, now=40 * 86400) == (500, 0)

    with Session(db_engine) as db:
        assert db.query(ReliableAction).count() == 1


def test_outbox_retention_keeps_unresolved_and_unconfirmed_attachment(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    now = datetime(2026, 9, 24, tzinfo=UTC).timestamp()
    old = now - 8 * 86400
    message = TaskMessage(
        id="retention-guard-message",
        issue_key="ROBOPARK-9",
        kind="user",
        author_name="worker",
        text="required",
        sync_state="pending",
        created_at=old,
        updated_at=old,
    )
    db_session.add(message)
    for state, result in (("pending", None), ("needs_attention", None), ("succeeded", None)):
        action = ReliableAction(
            id=f"guard-{state}",
            actor_user_id=seed_mechanic.id,
            resource_type="tracker_issue",
            resource_id="ROBOPARK-9",
            action="attach",
            idempotency_key=f"guard-{state}-key",
            payload_hash="0" * 64,
            payload_json="{}",
            state=state,
            result_json=result,
            next_attempt_at=0,
            created_at=old,
            updated_at=old,
        )
        db_session.add(action)
        blob = tmp_path / f"{state}.blob"
        blob.write_bytes(b"required")
        db_session.add(
            TaskAttachment(
                id=action.id,
                message_id=message.id,
                blob_name=blob.name,
                original_name=blob.name,
                mime_type="image/png",
                size_bytes=8,
                sha256="0" * 64,
                created_at=old,
                uploaded_at=old,
            )
        )
    db_session.commit()
    monkeypatch.setattr(cache_cleanup, "staged_attachments_root", lambda: tmp_path)
    assert cache_cleanup.prune_tracker_outbox(db_session, now=now) == (0, 0)
    assert all(
        (tmp_path / f"{state}.blob").read_bytes() == b"required"
        for state in ("pending", "needs_attention", "succeeded")
    )


def test_normal_and_pressure_retention_advance_past_protected_prefix(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    now = time.time()
    old = now - 8 * 86400
    pending = TaskMessage(
        id="prefix-pending",
        issue_key="ROBOPARK-9",
        kind="user",
        author_name="worker",
        text="pending",
        sync_state="pending",
        created_at=old,
        updated_at=old,
    )
    synced = TaskMessage(
        id="prefix-synced",
        issue_key="ROBOPARK-9",
        kind="system",
        author_name="system",
        text="synced",
        sync_state="synced",
        created_at=old,
        updated_at=old,
    )
    db_session.add_all([pending, synced])
    for index in range(502):
        confirmed = index >= 500
        action = ReliableAction(
            id=f"prefix-{index:04d}",
            actor_user_id=seed_mechanic.id,
            resource_type="tracker_issue",
            resource_id="ROBOPARK-9",
            action="attach",
            idempotency_key=f"prefix-key-{index}",
            payload_hash="0" * 64,
            payload_json="{}",
            state="succeeded",
            result_json='{"attachment_id":"remote","external_id":"link"}' if confirmed else "{}",
            next_attempt_at=0,
            created_at=old,
            updated_at=old,
        )
        db_session.add(action)
        db_session.add(
            TaskAttachment(
                id=action.id,
                message_id=synced.id,
                blob_name=f"prefix-{index:04d}.blob",
                original_name="upload.bin",
                mime_type="application/octet-stream",
                size_bytes=1,
                sha256="0" * 64,
                created_at=old,
                uploaded_at=old + index,
            )
        )
    db_session.commit()
    monkeypatch.setattr(cache_cleanup, "staged_attachments_root", lambda: tmp_path)
    for index in range(500, 502):
        (tmp_path / f"prefix-{index:04d}.blob").write_bytes(b"x")
    assert cache_cleanup.prune_tracker_outbox(db_session, now=now)[1] == 2
    assert db_session.get(TaskAttachment, "prefix-0000") is not None

    # A new confirmed copy behind the same protected prefix must also be reachable in pressure mode.
    extra = ReliableAction(
        id="prefix-pressure",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-9",
        action="attach",
        idempotency_key="prefix-pressure-key",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        result_json='{"attachment_id":"remote","external_id":"link"}',
        next_attempt_at=0,
        created_at=old,
        updated_at=old,
    )
    db_session.add(extra)
    db_session.add(
        TaskAttachment(
            id=extra.id,
            message_id=synced.id,
            blob_name="prefix-pressure.blob",
            original_name="upload.bin",
            mime_type="application/octet-stream",
            size_bytes=1,
            sha256="0" * 64,
            created_at=old,
            uploaded_at=old + 1000,
        )
    )
    db_session.commit()
    (tmp_path / "prefix-pressure.blob").write_bytes(b"x")
    report = cache_cleanup.cleanup_confirmed_tracker_copies(
        db_session,
        budget=cache_cleanup.StorageBudget(100, 0, minimum_free_bytes=1),
        max_deletions=1,
    )
    assert report["deleted_count"] == 1
    assert db_session.get(TaskAttachment, extra.id) is None


def test_outbox_retention_preserves_active_claim_start_action(
    db_session, seed_mechanic, seed_park_with_tracker
):
    now = datetime(2026, 9, 24, tzinfo=UTC).timestamp()
    action = ReliableAction(
        id="claim-start-action",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-50",
        action="start",
        idempotency_key="claim-start-action-key",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        next_attempt_at=0,
        created_at=now - 40 * 86400,
        updated_at=now - 40 * 86400,
    )
    db_session.add(action)
    db_session.add(
        TrackerClaim(
            issue_key="ROBOPARK-50",
            park_id=seed_park_with_tracker.id,
            owner_user_id=seed_mechanic.id,
            updated_by_user_id=seed_mechanic.id,
            state="active",
            start_action_id=action.id,
            updated_at=now,
        )
    )
    db_session.commit()
    assert cache_cleanup.prune_tracker_outbox(db_session, now=now) == (0, 0)
    assert db_session.get(ReliableAction, action.id) is not None


def test_pressure_cleanup_preserves_recent_confirmed_upload(
    db_session, seed_mechanic, tmp_path, monkeypatch
):
    now = time.time()
    action = ReliableAction(
        id="recent-pressure-action",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-51",
        action="attach",
        idempotency_key="recent-pressure-action-key",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        result_json='{"attachment_id":"remote-a","external_id":"remote-c"}',
        next_attempt_at=0,
        created_at=now - 86400,
        updated_at=now - 86400,
    )
    message = TaskMessage(
        id="recent-pressure-message",
        issue_key="ROBOPARK-51",
        kind="system",
        author_name="system",
        text="synced",
        sync_state="synced",
        created_at=now - 86400,
        updated_at=now - 86400,
    )
    db_session.add_all([action, message])
    db_session.flush()
    db_session.add(
        TaskAttachment(
            id=action.id,
            message_id=message.id,
            blob_name="recent-upload",
            original_name="recent.png",
            mime_type="image/png",
            size_bytes=4,
            sha256="0" * 64,
            created_at=now - 86400,
            uploaded_at=now - 86400,
        )
    )
    db_session.commit()
    (tmp_path / "recent-upload").write_bytes(b"keep")
    monkeypatch.setattr(cache_cleanup, "staged_attachments_root", lambda: tmp_path)
    report = cache_cleanup.cleanup_confirmed_tracker_copies(
        db_session, budget=cache_cleanup.StorageBudget(100, 0, minimum_free_bytes=1)
    )
    assert report["deleted_count"] == 0
    assert (tmp_path / "recent-upload").read_bytes() == b"keep"
    assert db_session.get(TaskAttachment, action.id) is not None


def test_pressure_coordinator_uses_real_owner_paths_in_order(
    db_engine, db_session, seed_mechanic, test_settings, tmp_path, monkeypatch
):
    from robopark_api.services import report_attachments, storage_retention
    from robopark_api.services.live_merge import LiveMergeStore

    data = tmp_path / "data"
    live_root = data / "live-merge"
    namespace = live_root / "tracker"
    namespace.mkdir(parents=True)
    cache_file = namespace / "cache.json"
    cache_file.write_text("{}")
    cache_files = [cache_file]
    for index in range(128):
        path = namespace / f"cache-{index:03d}.json"
        path.write_text("{}")
        cache_files.append(path)
    report_root = data / "report-attachments"
    staging = report_root / ".delete-staging"
    staging.mkdir(parents=True)
    report_file = staging / "deleted.log"
    report_file.write_bytes(b"report")
    os.utime(report_file, (1, 1))
    uploads = data / "task-attachments"
    uploads.mkdir(parents=True)
    upload = uploads / "confirmed-upload"
    upload.write_bytes(b"upload")
    unconfirmed = uploads / "unconfirmed-upload"
    unconfirmed.write_bytes(b"keep")

    action = ReliableAction(
        id="pressure-confirmed",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="attach",
        idempotency_key="pressure-confirmed-0001",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        result_json='{"attachment_id":"remote-a","external_id":"remote-c"}',
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    message = TaskMessage(
        id="pressure-message",
        issue_key="ROBOPARK-1",
        kind="system",
        author_name="system",
        text="audit",
        sync_state="synced",
        action_id=action.id,
        created_at=1,
        updated_at=1,
    )
    db_session.add_all([action, message])
    db_session.flush()
    db_session.add(
        TaskAttachment(
            id=action.id,
            message_id=message.id,
            blob_name=upload.name,
            original_name="upload.bin",
            mime_type="application/octet-stream",
            size_bytes=6,
            sha256="1" * 64,
            created_at=1,
            uploaded_at=2,
        )
    )
    db_session.commit()

    settings = test_settings.model_copy(
        update={"host_data_path": str(data), "ops_dir": str(tmp_path / "ops")}
    )
    store = LiveMergeStore(live_root)
    calls = []
    real_prune = store.prune
    real_reports = report_attachments.prune_deleted_report_files
    real_tracker = cache_cleanup.cleanup_confirmed_tracker_copies

    def cache_owner(**kwargs):
        assert kwargs["max_deletions"] == 128
        calls.append("cache_tmp")
        return real_prune(**kwargs)

    def report_owner(**kwargs):
        calls.append("diagnostics_logs")
        return real_reports(**kwargs)

    def tracker_owner(*args, **kwargs):
        calls.append("confirmed_tracker")
        return real_tracker(*args, **kwargs)

    def budget_for_path(cls, path):
        pressure = any(item.exists() for item in (*cache_files, report_file, upload))
        return cls(100, 0 if pressure else 100, minimum_free_bytes=1)

    monkeypatch.setattr(cache_cleanup, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_cleanup, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(store, "prune", cache_owner)
    monkeypatch.setattr(report_attachments, "attachments_root", lambda: report_root)
    monkeypatch.setattr(cache_cleanup, "prune_deleted_report_files", report_owner)
    monkeypatch.setattr(cache_cleanup, "staged_attachments_root", lambda: uploads)
    monkeypatch.setattr(cache_cleanup, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(cache_cleanup, "cleanup_confirmed_tracker_copies", tracker_owner)
    monkeypatch.setattr(storage_retention.StorageBudget, "for_path", classmethod(budget_for_path))

    report = cache_cleanup.cleanup_storage_pressure(now=100)

    assert not any(path.exists() for path in cache_files)
    assert not report_file.exists()
    assert not upload.exists()
    assert unconfirmed.read_bytes() == b"keep"
    assert (
        calls.index("cache_tmp")
        < calls.index("diagnostics_logs")
        < calls.index("confirmed_tracker")
    )
    assert report["pressure"] is False
    assert report["owners"]["cache_tmp"]["deleted_count"] == 129
    assert report["owners"]["cache_tmp"]["batches"] == 3
    assert report["owners"]["confirmed_tracker"]["deleted_count"] == 1
    with Session(db_engine) as db:
        assert db.get(ReliableAction, action.id) is not None
        assert db.get(TaskAttachment, action.id) is None


def test_staged_root_factory_keeps_symlink_visible_to_no_follow_guard(
    db_engine, db_session, seed_mechanic, tmp_path, monkeypatch
):
    from robopark_api.config import reset_settings_cache

    protected = tmp_path / "protected"
    actual_root = protected / "task-attachments"
    actual_root.mkdir(parents=True)
    upload = actual_root / "confirmed-upload"
    upload.write_bytes(b"outside")
    data_alias = tmp_path / "data"
    data_alias.symlink_to(protected, target_is_directory=True)
    configured = data_alias / "task-attachments"
    monkeypatch.setenv("STAGED_ATTACHMENTS_DIR", str(configured))
    reset_settings_cache()

    action = ReliableAction(
        id="symlink-confirmed",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="attach",
        idempotency_key="symlink-confirmed-0001",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    message = TaskMessage(
        id="symlink-message",
        issue_key="ROBOPARK-1",
        kind="system",
        author_name="system",
        text="audit",
        sync_state="synced",
        action_id=action.id,
        created_at=1,
        updated_at=1,
    )
    db_session.add_all([action, message])
    db_session.flush()
    db_session.add(
        TaskAttachment(
            id=action.id,
            message_id=message.id,
            blob_name=upload.name,
            original_name="upload.bin",
            mime_type="application/octet-stream",
            size_bytes=7,
            sha256="1" * 64,
            created_at=1,
            uploaded_at=2,
        )
    )
    db_session.commit()

    with Session(db_engine) as db:
        report = cache_cleanup.cleanup_confirmed_tracker_copies(
            db,
            budget=cache_cleanup.StorageBudget(100, 0, minimum_free_bytes=1),
        )

    assert report["deleted_count"] == 0
    assert report["blocked"] is True
    assert upload.read_bytes() == b"outside"
    with Session(db_engine) as db:
        assert db.get(TaskAttachment, action.id) is not None


def test_confirmed_tracker_cleanup_addresses_eligible_name_beyond_protected_prefix(
    db_engine, db_session, seed_mechanic, tmp_path, monkeypatch
):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    protected = []
    for index in range(4097):
        path = upload_root / f"protected-{index:04d}"
        path.write_bytes(b"keep")
        protected.append(path)
    upload = upload_root / "zz-confirmed-upload"
    upload.write_bytes(b"delete")

    action = ReliableAction(
        id="addressed-confirmed",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="attach",
        idempotency_key="addressed-confirmed-0001",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        result_json='{"attachment_id":"remote-a","external_id":"remote-c"}',
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    message = TaskMessage(
        id="addressed-message",
        issue_key="ROBOPARK-1",
        kind="system",
        author_name="system",
        text="audit",
        sync_state="synced",
        action_id=action.id,
        created_at=1,
        updated_at=1,
    )
    db_session.add_all([action, message])
    db_session.flush()
    db_session.add(
        TaskAttachment(
            id=action.id,
            message_id=message.id,
            blob_name=upload.name,
            original_name="upload.bin",
            mime_type="application/octet-stream",
            size_bytes=6,
            sha256="1" * 64,
            created_at=1,
            uploaded_at=2,
        )
    )
    db_session.commit()
    monkeypatch.setattr(cache_cleanup, "staged_attachments_root", lambda: upload_root)

    with Session(db_engine) as db:
        report = cache_cleanup.cleanup_confirmed_tracker_copies(
            db,
            budget=cache_cleanup.StorageBudget(100, 0, minimum_free_bytes=1),
            max_deletions=1,
            max_scanned_entries=1,
        )

    assert report["deleted"] == [{"category": "confirmed_tracker", "path": upload.name, "bytes": 6}]
    assert report["scanned_count"] == 1
    assert report["partial"] is False
    assert not upload.exists()
    assert all(path.read_bytes() == b"keep" for path in protected)
    with Session(db_engine) as db:
        assert db.get(ReliableAction, action.id) is not None
        assert db.get(TaskAttachment, action.id) is None


def test_pressure_coordinator_bounds_protected_tracker_scan_by_absolute_deadline(
    db_engine, db_session, seed_mechanic, test_settings, tmp_path, monkeypatch
):
    from robopark_api.services import storage_retention

    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    upload = upload_root / "confirmed-upload"
    upload.write_bytes(b"keep")
    action = ReliableAction(
        id="bounded-scan-confirmed",
        actor_user_id=seed_mechanic.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="attach",
        idempotency_key="bounded-scan-confirmed-0001",
        payload_hash="0" * 64,
        payload_json="{}",
        state="succeeded",
        result_json='{"attachment_id":"remote-a","external_id":"remote-c"}',
        next_attempt_at=0,
        created_at=1,
        updated_at=1,
    )
    message = TaskMessage(
        id="bounded-scan-message",
        issue_key="ROBOPARK-1",
        kind="system",
        author_name="system",
        text="audit",
        sync_state="synced",
        action_id=action.id,
        created_at=1,
        updated_at=1,
    )
    db_session.add_all([action, message])
    db_session.flush()
    db_session.add(
        TaskAttachment(
            id=action.id,
            message_id=message.id,
            blob_name="confirmed-upload",
            original_name="upload.bin",
            mime_type="application/octet-stream",
            size_bytes=7,
            sha256="1" * 64,
            created_at=1,
            uploaded_at=2,
        )
    )
    db_session.commit()

    settings = test_settings.model_copy(
        update={"host_data_path": str(tmp_path), "ops_dir": str(tmp_path / "ops")}
    )
    ticks = iter(index / 10 for index in range(1000))
    monkeypatch.setattr(cache_cleanup, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_cleanup, "get_live_merge_store", lambda: None)
    monkeypatch.setattr(cache_cleanup, "prune_deleted_report_files", lambda **kwargs: 0)
    monkeypatch.setattr(cache_cleanup, "staged_attachments_root", lambda: upload_root)
    monkeypatch.setattr(cache_cleanup, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(cache_cleanup, "_write_pressure_report", lambda report: None)
    monkeypatch.setattr(
        storage_retention.StorageBudget,
        "for_path",
        classmethod(lambda cls, path: cls(100, 0, minimum_free_bytes=1)),
    )
    monkeypatch.setattr(cache_cleanup.time, "monotonic", lambda: next(ticks))

    report = cache_cleanup.cleanup_storage_pressure(now=100)

    assert report["partial"] is True
    assert report["stop_reason"] == "time_budget"
    assert report["scanned_count"] == 1
    assert report["deleted_count"] == 0
    assert upload.read_bytes() == b"keep"
    with Session(db_engine) as db:
        assert db.get(TaskAttachment, action.id) is not None


def test_pressure_coordinator_yields_at_total_deletion_budget(tmp_path, monkeypatch):
    from robopark_api.services import storage_retention

    class Store:
        def __init__(self):
            self.limits = []

        def prune(self, **kwargs):
            self.limits.append(kwargs["max_deletions"])
            return kwargs["max_deletions"]

    store = Store()
    settings = type(
        "Settings",
        (),
        {"host_data_path": str(tmp_path), "ops_dir": str(tmp_path / "ops")},
    )()
    monkeypatch.setattr(cache_cleanup, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_cleanup, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(
        storage_retention.StorageBudget,
        "for_path",
        classmethod(lambda cls, path: cls(100, 0, minimum_free_bytes=1)),
    )
    monkeypatch.setattr(cache_cleanup, "_write_pressure_report", lambda report: None)

    report = cache_cleanup.cleanup_storage_pressure(now=100)

    assert store.limits == [128, 128, 128, 128]
    assert report["deleted_count"] == 512
    assert report["iterations"] == 4
    assert report["partial"] is True
    assert report["stop_reason"] == "deletion_budget"


def test_pressure_coordinator_yields_at_time_budget_before_owner(tmp_path, monkeypatch):
    from robopark_api.services import storage_retention

    settings = type(
        "Settings",
        (),
        {"host_data_path": str(tmp_path), "ops_dir": str(tmp_path / "ops")},
    )()

    class Store:
        def prune(self, **kwargs):
            pytest.fail("owner ran")

    monkeypatch.setattr(cache_cleanup, "get_settings", lambda: settings)
    monkeypatch.setattr(cache_cleanup, "get_live_merge_store", Store)
    monkeypatch.setattr(
        storage_retention.StorageBudget,
        "for_path",
        classmethod(lambda cls, path: cls(100, 0, minimum_free_bytes=1)),
    )
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(cache_cleanup.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(cache_cleanup, "_write_pressure_report", lambda report: None)

    report = cache_cleanup.cleanup_storage_pressure(now=100)

    assert report["deleted_count"] == 0
    assert report["iterations"] == 0
    assert report["partial"] is True
    assert report["stop_reason"] == "time_budget"
