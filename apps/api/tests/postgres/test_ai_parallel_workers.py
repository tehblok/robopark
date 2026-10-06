"""Real database claims: independent replies overlap, each job runs once."""

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.ai_models import AIConfig, AIConversation, AIJob, AIMessage
from robopark_api.models import Park, User
from robopark_api.services.ai import jobs
from robopark_api.services.rbac import get_role_by_slug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def test_parallel_workers_claim_each_job_once(migrated_engine, test_settings, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        jobs.policy,
        "host_status",
        lambda _settings: {
            "supported": True,
            "enabled": True,
            "ready": True,
            "backend": "cuda",
        },
    )
    monkeypatch.setattr(jobs, "host_maintenance_active", lambda _settings: False)
    unique = uuid4().hex
    factory = sessionmaker(bind=migrated_engine)
    job_ids = []
    with Session(migrated_engine) as db:
        ensure_rbac_catalog(db)
        config = db.get(AIConfig, 1)
        if config is None:
            db.add(AIConfig(id=1, enabled=True))
        else:
            config.enabled = True
        user = User(
            username=unique,
            password_hash="unused",
            role_id=get_role_by_slug(db, "admin").id,
            is_active=True,
            access_status="approved",
        )
        park = Park(name=unique, tag=unique, is_active=True)
        db.add_all([user, park])
        db.flush()
        for index in range(2):
            conversation = AIConversation(owner_id=user.id, park_id=park.id)
            db.add(conversation)
            db.flush()
            db.add(
                AIMessage(conversation_id=conversation.id, role="user", content=f"check-{index}")
            )
            job = AIJob(
                owner_id=user.id,
                park_id=park.id,
                conversation_id=conversation.id,
                kind="chat",
                payload={"request": {"content": f"check-{index}"}},
                idempotency_key=f"{unique}-{index}",
            )
            db.add(job)
            db.flush()
            job_ids.append(job.id)
        db.commit()
    barrier = threading.Barrier(2, timeout=5)
    calls = []

    def complete(_settings, messages):
        calls.append(messages[-1]["content"])
        barrier.wait()
        return "Готово"

    monkeypatch.setattr(jobs.runtime, "complete", complete)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: jobs.process_job(factory, test_settings), range(3)))
    assert sorted(calls) == ["check-0", "check-1"]
    assert results.count(True) == 2
    with factory() as db:
        assert [db.get(AIJob, job_id).state for job_id in job_ids] == ["succeeded", "succeeded"]
