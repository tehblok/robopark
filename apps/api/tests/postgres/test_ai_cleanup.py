"""History deletion uses the production SQL dialect and its real foreign keys."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.ai_models import AIConversation, AIJob, AIMessage
from robopark_api.models import Park, Role, User
from robopark_api.routers.ai_admin import _purge_history

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def test_real_postgres_history_cleanup_keeps_inflight_conversations(migrated_engine):  # noqa: F811
    with Session(migrated_engine) as db:
        prefix = uuid4().hex[:24]
        role = db.scalar(select(Role).limit(1))
        user = User(username=prefix, password_hash="unused", role_id=role.id)
        park = Park(name="Cleanup", tag=prefix)
        db.add_all([user, park])
        db.flush()
        idle, busy = prefix + "-idle", prefix + "-busy"
        for identity in (idle, busy):
            db.add(AIConversation(id=identity, owner_id=user.id, park_id=park.id, updated_at=1))
        db.flush()
        for identity, state in ((idle, "succeeded"), (busy, "running")):
            db.add(AIMessage(conversation_id=identity, role="user", content="private input"))
            db.add(
                AIJob(
                    owner_id=user.id,
                    conversation_id=identity,
                    kind="chat",
                    state=state,
                    idempotency_key=identity,
                    updated_at=1,
                )
            )
        db.add(
            AIJob(
                owner_id=user.id,
                kind="draft",
                state="succeeded",
                idempotency_key=prefix,
                updated_at=1,
                payload={"request": "private prompt"},
            )
        )
        db.flush()

        result = _purge_history(db, 2)

        assert result == {
            "deleted": 1,
            "conversations_deleted": 1,
            "jobs_deleted": 2,
            "messages_deleted": 1,
        }
        db.expire_all()
        assert db.get(AIConversation, idle) is None
        assert db.get(AIConversation, busy) is not None
        assert list(db.scalars(select(AIJob.conversation_id).where(AIJob.owner_id == user.id))) == [
            busy
        ]
        assert list(
            db.scalars(
                select(AIMessage.conversation_id).where(AIMessage.conversation_id.in_((idle, busy)))
            )
        ) == [busy]
