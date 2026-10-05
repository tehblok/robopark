"""PostgreSQL serialization coverage for AI job source boundaries."""

import json
import threading
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.ai_models import AIConfig, AIDocument, AIJob
from robopark_api.models import Park, User
from robopark_api.services.ai import jobs, knowledge
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.rbac import get_role_by_slug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def test_document_delete_waits_for_source_validated_result_publication(
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
        ensure_rbac_catalog(db)
        config = db.get(AIConfig, 1)
        if config is None:
            config = AIConfig(id=1)
            db.add(config)
        config.enabled = True
        user = User(
            username="publish-" + unique,
            password_hash="unused",
            role_id=get_role_by_slug(db, "admin").id,
            access_status="approved",
            is_active=True,
        )
        park = Park(name="publish-" + unique, tag="publish-" + unique, is_active=True)
        db.add_all([user, park])
        db.flush()
        document = AIDocument(
            source_key=unique.ljust(64, "0"),
            fingerprint=unique.ljust(64, "1"),
            title="Camera guide",
            content="Check the camera cable",
            kind="manual",
            state="active",
            trust="instruction",
            park_id=park.id,
            source_ref="test:" + unique,
        )
        job = AIJob(
            owner_id=user.id,
            park_id=park.id,
            kind="draft",
            state="running",
            payload={"request": {"instruction": "Draft repair", "kind": "automation"}},
            idempotency_key=unique,
        )
        db.add_all([document, job])
        db.commit()
        document_id, revision, job_id = document.id, document.revision, job.id

    factory = sessionmaker(bind=migrated_engine, future=True)
    source_validated = threading.Event()
    release_publication = threading.Event()
    delete_started = threading.Event()
    delete_finished = threading.Event()
    errors = []
    original_sources_valid = jobs.sources_valid

    def pause_after_validation(db, user, sources):
        valid = original_sources_valid(db, user, sources)
        if threading.current_thread().name == "publish-ai-result":
            source_validated.set()
            if not release_publication.wait(3):
                raise TimeoutError("test did not release publication")
        return valid

    monkeypatch.setattr(jobs, "sources_valid", pause_after_validation)
    sources = [{"id": document_id, "revision": revision}]

    def publish():
        try:
            jobs._publish_result(
                factory,
                test_settings,
                job_id,
                "draft",
                sources,
                {"proposal": {}, "explanation": "ok"},
                None,
                None,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def delete():
        try:
            delete_started.set()
            with factory() as db, database_idempotency_lock(db, "ai-knowledge"):
                knowledge.remove(db, db.get(AIDocument, document_id))
            delete_finished.set()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    publisher = threading.Thread(target=publish, name="publish-ai-result")
    deleter = threading.Thread(target=delete, name="delete-ai-document")
    publisher.start()
    assert source_validated.wait(3)
    deleter.start()
    assert delete_started.wait(3)
    try:
        assert not delete_finished.wait(0.25)
    finally:
        release_publication.set()
        publisher.join(5)
        deleter.join(5)

    assert not publisher.is_alive() and not deleter.is_alive()
    assert not errors
    with factory() as db:
        assert db.get(AIJob, job_id).state == "succeeded"
        assert db.get(AIDocument, document_id).state == "deleted"


def test_document_delete_waits_for_claim_then_invalidates_publication(
    migrated_engine,  # noqa: F811
    test_settings,
    tmp_path,
    monkeypatch,
):
    runtime = tmp_path / "ai-runtime-claim.json"
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
        ensure_rbac_catalog(db)
        config = db.get(AIConfig, 1)
        if config is None:
            config = AIConfig(id=1)
            db.add(config)
        config.enabled = True
        user = User(
            username="claim-" + unique,
            password_hash="unused",
            role_id=get_role_by_slug(db, "admin").id,
            access_status="approved",
            is_active=True,
        )
        park = Park(name="claim-" + unique, tag="claim-" + unique, is_active=True)
        db.add_all([user, park])
        db.flush()
        document = AIDocument(
            source_key=unique.ljust(64, "0"),
            fingerprint=unique.ljust(64, "1"),
            title="Camera guide",
            content="Check the camera cable",
            kind="manual",
            state="active",
            trust="instruction",
            park_id=park.id,
            source_ref="test:" + unique,
        )
        job = AIJob(
            owner_id=user.id,
            park_id=park.id,
            kind="draft",
            state="queued",
            payload={"request": {"instruction": "Draft repair", "kind": "automation"}},
            idempotency_key=unique,
        )
        db.add_all([document, job])
        db.commit()
        document_id, revision, job_id = document.id, document.revision, job.id

    factory = sessionmaker(bind=migrated_engine, future=True)
    claim_validated = threading.Event()
    release_claim = threading.Event()
    delete_started = threading.Event()
    delete_finished = threading.Event()
    errors = []
    original_validate = jobs._validate_claim
    sources = [{"id": document_id, "revision": revision}]

    monkeypatch.setattr(
        jobs,
        "_prepare",
        lambda _db, _settings, _job, _issue: ([{"role": "user", "content": "x"}], sources),
    )

    def pause_after_claim_validation(db, settings, job, current_sources, issue_snapshot):
        original_validate(db, settings, job, current_sources, issue_snapshot)
        claim_validated.set()
        if not release_claim.wait(3):
            raise TimeoutError("test did not release claim")

    monkeypatch.setattr(jobs, "_validate_claim", pause_after_claim_validation)

    def complete(*_args):
        if not delete_finished.wait(3):
            raise TimeoutError("document delete did not finish")
        return '{"proposal": {}, "explanation": "outdated"}'

    monkeypatch.setattr(jobs.runtime, "complete", complete)

    def process():
        try:
            jobs.process_job(factory, test_settings)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def delete():
        try:
            delete_started.set()
            with factory() as db, database_idempotency_lock(db, "ai-knowledge"):
                knowledge.remove(db, db.get(AIDocument, document_id))
            delete_finished.set()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    processor = threading.Thread(target=process, name="claim-ai-job")
    deleter = threading.Thread(target=delete, name="delete-claimed-source")
    processor.start()
    assert claim_validated.wait(3)
    deleter.start()
    assert delete_started.wait(3)
    try:
        assert not delete_finished.wait(0.25)
    finally:
        release_claim.set()
        processor.join(5)
        deleter.join(5)

    assert not processor.is_alive() and not deleter.is_alive()
    assert not errors
    with factory() as db:
        final = db.get(AIJob, job_id)
        assert (final.state, final.error, final.result) == (
            "cancelled",
            "ai_sources_changed",
            None,
        )
