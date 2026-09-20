import asyncio
import os
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from robopark_api import main
from robopark_api.models import AuthThrottleState
from robopark_api.services import cache_cleanup
from robopark_api.task_workflow_models import (
    OfflineSyncReceipt,
    ReliableAction,
    TaskAttachment,
    TaskMessage,
)


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
        cache_cleanup.push,
        "prune_notification_data",
        lambda session, **kwargs: (
            calls.append(("notifications", session, kwargs))
            or {"subscriptions": 1, "notifications": 2}
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
        ("notifications", db, {"now": now}),
        ("schedules", db, {"now": now}),
        ("outbox", db, {"now": now.timestamp()}),
        ("pending-reports", db),
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


def test_cleanup_does_not_delete_auth_throttle_row_renewed_after_selection(
    db_session, db_engine
):
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
def test_lifespan_starts_cleanup_only_for_job_lease_owner(
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
        assert session_factory is main.SessionLocal
        outbox_started.set()
        await stop_event.wait()

    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(main, "live_merge_enabled", lambda: merge_enabled)
    monkeypatch.setattr(main, "JobLease", Lease)
    monkeypatch.setattr(main, "run_keepalive_loop", idle_loop)
    monkeypatch.setattr(main, "run_blocker_history_loop", idle_loop)
    monkeypatch.setattr(main, "run_session_cleanup_loop", idle_loop)
    monkeypatch.setattr(main, "run_cache_cleanup_loop", cleanup_loop)
    monkeypatch.setattr(main, "run_tracker_outbox_loop", outbox_loop)

    with TestClient(main.create_app()):
        assert lease_attempted.wait(timeout=1)
        if won_lease:
            assert cleanup_started.wait(timeout=1)
            assert outbox_started.wait(timeout=1)
        else:
            assert not cleanup_started.is_set()
            assert not outbox_started.is_set()

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


def test_lifespan_awaits_blocking_outbox_before_releasing_job_lease(
    db_engine, test_settings, monkeypatch
):
    entered = threading.Event()
    close_context = threading.Event()
    outbox_started = threading.Event()
    finish_outbox = threading.Event()
    lease_released = threading.Event()
    app_holder = []

    class Lease:
        def __init__(self, root, name):
            pass

        def try_acquire(self):
            return True

        def release(self):
            lease_released.set()

    async def idle_loop(stop_event, **kwargs):
        await stop_event.wait()

    async def blocking_outbox(session_factory, stop_event, **kwargs):
        outbox_started.set()
        await asyncio.to_thread(finish_outbox.wait)
        assert stop_event.is_set()

    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)
    monkeypatch.setattr(main, "live_merge_enabled", lambda: True)
    monkeypatch.setattr(main, "JobLease", Lease)
    monkeypatch.setattr(main, "run_keepalive_loop", idle_loop)
    monkeypatch.setattr(main, "run_blocker_history_loop", idle_loop)
    monkeypatch.setattr(main, "run_session_cleanup_loop", idle_loop)
    monkeypatch.setattr(main, "run_cache_cleanup_loop", idle_loop)
    monkeypatch.setattr(main, "run_tracker_outbox_loop", blocking_outbox)

    def serve():
        app = main.create_app()
        app_holder.append(app)
        with TestClient(app):
            future = app.state.push_service._submit_deliveries(
                [("hash", "endpoint", "p256dh", "auth")],
                lambda _delivery: None,
                max_workers=1,
            )[0]
            future.result(timeout=1)
            entered.set()
            close_context.wait(timeout=2)

    thread = threading.Thread(target=serve)
    thread.start()
    assert entered.wait(timeout=1)
    assert outbox_started.wait(timeout=1)
    close_context.set()
    try:
        assert not lease_released.wait(timeout=0.1)
        assert not app_holder[0].state.push_service._delivery_closed
    finally:
        finish_outbox.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert lease_released.is_set()
    assert app_holder[0].state.push_service._delivery_closed
    assert app_holder[0].state.push_service._delivery_executor is None


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
