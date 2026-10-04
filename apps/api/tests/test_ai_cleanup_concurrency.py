"""History cleanup and enqueue linearize without orphaning conversation data."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from robopark_api import ai_schemas
from robopark_api.ai_models import AIConversation, AIJob, AIMessage
from robopark_api.routers import ai_admin
from robopark_api.services.ai import jobs
from robopark_api.services.database_locks import database_idempotency_lock


def test_enqueue_holding_queue_lock_survives_concurrent_history_cleanup(
    db_engine,
    db_session,
    seed_admin,
    seed_park_with_tracker,
    monkeypatch,
):
    old = time.time() - 40 * 86400
    conversation_id = "cleanup-enqueue-race"
    db_session.add(
        AIConversation(
            id=conversation_id,
            owner_id=seed_admin.id,
            park_id=seed_park_with_tracker.id,
            title="Old conversation",
            updated_at=old,
        )
    )
    db_session.add(
        AIMessage(conversation_id=conversation_id, role="assistant", content="old reply")
    )
    db_session.commit()

    enqueue_locked = threading.Event()
    allow_enqueue = threading.Event()
    cleanup_has_controls = threading.Event()
    cleanup_waiting_for_queue = threading.Event()

    @contextmanager
    def observed_enqueue_lock(db, key):
        with database_idempotency_lock(db, key):
            if key == "ai-queue":
                enqueue_locked.set()
                assert allow_enqueue.wait(5)
            yield

    @contextmanager
    def observed_cleanup_lock(db, key):
        if key == "ai-queue":
            cleanup_waiting_for_queue.set()
        with database_idempotency_lock(db, key):
            if key == "ai-controls":
                cleanup_has_controls.set()
            yield

    monkeypatch.setattr(jobs, "database_idempotency_lock", observed_enqueue_lock)
    monkeypatch.setattr(ai_admin, "database_idempotency_lock", observed_cleanup_lock)
    factory = sessionmaker(bind=db_engine, future=True)

    def enqueue():
        with factory() as db:
            actor = db.get(type(seed_admin), seed_admin.id)
            return jobs.enqueue(
                db,
                actor,
                kind="chat",
                conversation_id=conversation_id,
                park_id=seed_park_with_tracker.id,
                payload={"content": "new question"},
                key="cleanup-race-key",
            ).id

    def cleanup():
        with factory() as db:
            actor = db.get(type(seed_admin), seed_admin.id)
            return ai_admin.maintenance(
                ai_schemas.MaintenanceIn(kind="history", before_days=30), db, actor
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        enqueue_future = pool.submit(enqueue)
        assert enqueue_locked.wait(5)
        cleanup_future = pool.submit(cleanup)
        assert cleanup_has_controls.wait(5)
        assert cleanup_waiting_for_queue.wait(5)
        allow_enqueue.set()
        job_id = enqueue_future.result(timeout=5)
        cleanup_result = cleanup_future.result(timeout=5)

    with factory() as db:
        conversation = db.get(AIConversation, conversation_id)
        job = db.get(AIJob, job_id)
        messages = list(
            db.scalars(
                select(AIMessage)
                .where(AIMessage.conversation_id == conversation_id)
                .order_by(AIMessage.created_at, AIMessage.id)
            )
        )
        orphan_jobs = db.scalar(
            select(func.count())
            .select_from(AIJob)
            .outerjoin(AIConversation, AIConversation.id == AIJob.conversation_id)
            .where(AIJob.conversation_id.is_not(None), AIConversation.id.is_(None))
        )
        orphan_messages = db.scalar(
            select(func.count())
            .select_from(AIMessage)
            .outerjoin(AIConversation, AIConversation.id == AIMessage.conversation_id)
            .where(AIConversation.id.is_(None))
        )

    assert cleanup_result["conversations_deleted"] == 0
    assert conversation is not None
    assert job is not None and job.state == "queued"
    assert [message.content for message in messages] == ["old reply", "new question"]
    assert orphan_jobs == orphan_messages == 0
