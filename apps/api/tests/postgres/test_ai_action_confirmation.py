"""Two concurrent PostgreSQL confirmations authorize one durable effect."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.ai_models import AIAction, AIConfig, AIConversation, AIJob, AIScript
from robopark_api.models import Park, User
from robopark_api.services.ai import runtime, tool_actions
from robopark_api.services.rbac import get_role_by_slug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def test_two_confirmations_produce_one_script_deletion(
    migrated_engine,  # noqa: F811
    test_settings,
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "ai-actions-runtime.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "supported": True,
                "installed": True,
                "enabled": True,
                "ready": True,
                "backend": "cuda",
            }
        )
    )
    test_settings.ai_runtime_state_path = str(path)
    unique = uuid4().hex
    factory = sessionmaker(bind=migrated_engine)
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
        script = AIScript(name="Disposable", source="def main(data):\n    return data")
        db.add_all([user, park, script])
        db.flush()
        conversation = AIConversation(owner_id=user.id, park_id=park.id)
        db.add(conversation)
        db.flush()
        job = AIJob(
            owner_id=user.id,
            park_id=park.id,
            conversation_id=conversation.id,
            kind="chat",
            state="running",
            idempotency_key=unique,
            payload={"request": {"use_tools": True}, "sources": []},
        )
        db.add(job)
        db.commit()
        user_id, job_id, script_id = user.id, job.id, script.id
    monkeypatch.setattr(runtime, "context_tokens", lambda *args, **kwargs: (100, 8192))
    monkeypatch.setattr(
        runtime,
        "complete_turn",
        lambda *args: {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "delete_1",
                    "type": "function",
                    "function": {
                        "name": "script_delete",
                        "arguments": json.dumps({"script_id": script_id, "revision": 1}),
                    },
                }
            ],
        },
    )
    tool_actions.step(
        factory,
        test_settings,
        job_id,
        [{"role": "system", "content": "Help"}, {"role": "user", "content": "Delete script"}],
    )
    with factory() as db:
        receipt = db.scalar(select(AIAction).where(AIAction.job_id == job_id))
        action_id, digest = receipt.id, receipt.digest
        assert receipt.state == "waiting"

    def confirm():
        with factory() as db:
            return tool_actions.confirm(
                db, test_settings, db.get(User, user_id), action_id, digest
            ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(lambda _: confirm(), range(2))) == [job_id, job_id]
    with factory() as db:
        job = db.get(AIJob, job_id)
        assert job.state == "queued"
        job.state = "running"
        db.commit()
        messages = job.payload["tool_messages"]
    tool_actions.step(factory, test_settings, job_id, messages)
    with factory() as db:
        assert db.get(AIScript, script_id) is None
        assert db.get(AIAction, action_id).state == "succeeded"
        assert len(list(db.scalars(select(AIAction).where(AIAction.job_id == job_id)))) == 1
        # Don't leave queued work behind for other acceptance tests.
        db.get(AIJob, job_id).state = "succeeded"
        db.commit()
