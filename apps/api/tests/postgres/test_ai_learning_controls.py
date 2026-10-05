"""PostgreSQL advisory-lock coverage for the learning control boundary."""

import json
import threading
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.ai_models import AIConfig, AIDocument, AIEvent
from robopark_api.models import Park
from robopark_api.services.ai import learning

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def test_learning_rereads_disabled_config_after_postgres_controls_lock(
    migrated_engine,  # noqa: F811
    test_settings,
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "ai-runtime.json"
    runtime.write_text(
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
    test_settings.ai_runtime_state_path = str(runtime)
    unique = uuid4().hex
    with Session(migrated_engine) as db:
        previous = db.get(AIConfig, 1)
        previous_values = (
            None
            if previous is None
            else (previous.enabled, previous.learning_enabled, previous.revision)
        )
        if previous is None:
            previous = AIConfig(id=1)
            db.add(previous)
        previous.enabled = True
        previous.learning_enabled = True
        previous.revision = (previous.revision or 0) + 1
        park = Park(name="learning-" + unique, tag="learning-" + unique, is_active=True)
        db.add(park)
        db.flush()
        park_id = park.id
        event_key = unique + "-verified-close"
        db.add(
            AIEvent(
                key=event_key,
                park_id=park_id,
                occurred_at=1,
                payload={"issue_key": "R-1", "comment": "Replaced camera cable"},
            )
        )
        db.commit()

    attempted = threading.Event()
    finished = threading.Event()
    errors = []
    original_lock = learning.database_idempotency_lock

    @contextmanager
    def observed_lock(db, key):
        if key == "ai-controls" and threading.current_thread().name == "learning-postgres":
            attempted.set()
        with original_lock(db, key):
            yield

    monkeypatch.setattr(learning, "database_idempotency_lock", observed_lock)

    def process():
        try:
            with Session(migrated_engine) as db:
                learning.process_events(db, test_settings)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=process, name="learning-postgres")
    try:
        with Session(migrated_engine) as admin, original_lock(admin, "ai-controls"):
            thread.start()
            assert attempted.wait(3)
            assert not finished.wait(0.25)
            config = admin.get(AIConfig, 1, populate_existing=True)
            config.enabled = False
            config.revision += 1
            admin.commit()
        thread.join(5)
        assert not thread.is_alive()
        assert not errors
        with Session(migrated_engine) as db:
            assert db.get(AIConfig, 1).enabled is False
            assert db.get(AIEvent, event_key).processed is False
            assert (
                db.scalar(
                    select(AIDocument.id).where(AIDocument.source_ref == "repair:" + event_key)
                )
                is None
            )
    finally:
        if thread.is_alive():
            thread.join(5)
        with Session(migrated_engine) as db:
            config = db.get(AIConfig, 1)
            if previous_values is None:
                db.delete(config)
            else:
                config.enabled, config.learning_enabled, config.revision = previous_values
            park = db.get(Park, park_id)
            if park is not None:
                db.delete(park)
            db.commit()
