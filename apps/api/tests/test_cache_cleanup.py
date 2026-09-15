import asyncio
import threading
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from robopark_api import main
from robopark_api.services import cache_cleanup
from robopark_api.task_workflow_models import ReliableAction, TaskAttachment, TaskMessage


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

    assert cache_cleanup.prune_cache_once(now=now) == (4, 3)
    assert calls == [
        ("files", {"now": now.timestamp()}),
        ("unknowns", db, {"now": now}),
        ("outbox", db, {"now": now.timestamp()}),
    ]


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
