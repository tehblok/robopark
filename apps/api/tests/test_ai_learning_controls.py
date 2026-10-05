"""Learning batches serialize with administrative and knowledge changes."""

import json
import threading

from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.ai_models import AIConfig, AIDocument, AIEvent
from robopark_api.services.ai import knowledge, learning
from robopark_api.services.database_locks import database_idempotency_lock


def _enable_host(settings, tmp_path):
    path = tmp_path / "ai-runtime.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "supported": True,
                "installed": True,
                "enabled": True,
                "ready": True,
                "reason": None,
                "model": "bonsai",
                "backend": "cuda",
            }
        ),
        encoding="utf-8",
    )
    settings.ai_runtime_state_path = str(path)


def _stage_event(db_session, park_id):
    db_session.add_all(
        [
            AIConfig(id=1, enabled=True, learning_enabled=True, revision=1),
            AIEvent(
                key="verified-close",
                park_id=park_id,
                occurred_at=1,
                payload={"issue_key": "R-1", "comment": "Replaced camera cable"},
            ),
            AIDocument(
                source_key="0" * 64,
                fingerprint="1" * 64,
                title="Existing guide",
                content="Existing repair guidance",
                kind="manual",
                state="active",
                trust="verified",
                park_id=park_id,
                source_ref="manual:existing",
            ),
        ]
    )
    db_session.commit()


def _paused_processor(db_engine, settings):
    reached_policy = threading.Event()
    release_policy = threading.Event()
    processing_committed = threading.Event()
    errors = []

    def pause_event_query(state):
        if "ai_events" in str(state.statement) and not reached_policy.is_set():
            reached_policy.set()
            if not release_policy.wait(3):
                raise TimeoutError("test did not release learning batch")

    def process():
        try:
            with Session(db_engine) as db:
                sqlalchemy_event.listen(db, "after_commit", lambda _db: processing_committed.set())
                sqlalchemy_event.listen(db, "do_orm_execute", pause_event_query)
                learning.process_events(db, settings)
        except Exception as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    thread = threading.Thread(target=process, name="learning-batch")
    thread.start()
    assert reached_policy.wait(3)
    return thread, release_policy, processing_committed, errors


def test_successful_ai_disable_waits_for_inflight_learning_batch(
    db_session,
    db_engine,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
):
    _enable_host(test_settings, tmp_path)
    _stage_event(db_session, seed_park_with_tracker.id)
    processor, release, processing_committed, errors = _paused_processor(db_engine, test_settings)
    disable_started = threading.Event()
    disable_finished = threading.Event()

    def disable():
        with Session(db_engine) as db:
            disable_started.set()
            with database_idempotency_lock(db, "ai-controls"):
                config = db.get(AIConfig, 1)
                config.enabled = False
                config.revision += 1
                db.commit()
            disable_finished.set()

    thread = threading.Thread(target=disable, name="disable-ai")
    thread.start()
    assert disable_started.wait(3)
    try:
        assert not disable_finished.wait(0.25)
    finally:
        release.set()
        processor.join(3)
        thread.join(3)

    assert not errors
    assert processing_committed.is_set()
    assert disable_finished.is_set()
    db_session.expire_all()
    assert db_session.get(AIConfig, 1).enabled is False
    assert db_session.scalar(
        select(AIDocument.id).where(AIDocument.source_ref == "repair:verified-close")
    )


def test_knowledge_change_waits_for_inflight_learning_batch(
    db_session,
    db_engine,
    seed_park_with_tracker,
    test_settings,
    tmp_path,
):
    _enable_host(test_settings, tmp_path)
    _stage_event(db_session, seed_park_with_tracker.id)
    processor, release, processing_committed, errors = _paused_processor(db_engine, test_settings)
    change_started = threading.Event()
    change_finished = threading.Event()

    def change_knowledge():
        with Session(db_engine) as db:
            change_started.set()
            with database_idempotency_lock(db, "ai-knowledge"):
                row = db.scalar(
                    select(AIDocument).where(AIDocument.source_ref == "manual:existing")
                )
                knowledge.remove(db, row)
            change_finished.set()

    thread = threading.Thread(target=change_knowledge, name="change-knowledge")
    thread.start()
    assert change_started.wait(3)
    try:
        assert not change_finished.wait(0.25)
    finally:
        release.set()
        processor.join(3)
        thread.join(3)

    assert not errors
    assert processing_committed.is_set()
    assert change_finished.is_set()
    db_session.expire_all()
    removed = db_session.scalar(
        select(AIDocument).where(AIDocument.source_ref == "manual:existing")
    )
    assert removed.state == "deleted" and removed.content == ""
