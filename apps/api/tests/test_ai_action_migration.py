"""Upgrading preserves chat history; deleting a job retains its effect receipt."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from robopark_api.ai_models import AIAction, AIConversation, AIJob, AIMessage
from robopark_api.db import configure_engine
from robopark_api.models import Park, User
from robopark_api.services.rbac import get_role_by_slug
from robopark_api.services.rbac_seed import ensure_rbac_catalog


def test_upgrade_existing_chat_and_keep_receipt_after_job_deletion(
    sqlite_database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0058_ai_bundle_receipts")
    engine = configure_engine(sqlite_database_url)
    with Session(engine) as db:
        ensure_rbac_catalog(db)
        user = User(
            username="migration",
            password_hash="unused",
            role_id=get_role_by_slug(db, "admin").id,
            is_active=True,
            access_status="approved",
        )
        park = Park(name="Migration", tag="migration")
        db.add_all([user, park])
        db.flush()
        conversation = AIConversation(owner_id=user.id, park_id=park.id)
        db.add(conversation)
        db.flush()
        message = AIMessage(
            conversation_id=conversation.id, role="user", content="Сохранить историю", sources=[]
        )
        job = AIJob(
            owner_id=user.id,
            park_id=park.id,
            conversation_id=conversation.id,
            kind="chat",
            state="succeeded",
            idempotency_key="legacy",
            payload={},
        )
        db.add_all([message, job])
        db.commit()
        user_id, park_id, message_id, job_id = user.id, park.id, message.id, job.id
    command.upgrade(config, "head")
    with Session(engine) as db:
        assert db.get(AIMessage, message_id).content == "Сохранить историю"
        receipt = AIAction(
            job_id=job_id,
            owner_id=user_id,
            park_id=park_id,
            ordinal=0,
            call_id="c1",
            tool="script_create",
            arguments={},
            expected={},
            preview="Created",
            digest="a" * 64,
            state="succeeded",
            result={"id": "script"},
            expires_at=100,
        )
        db.add(receipt)
        db.commit()
        receipt_id = receipt.id
        db.delete(db.get(AIJob, job_id))
        db.commit()
        db.expire_all()
        assert db.get(AIAction, receipt_id).job_id is None
        assert db.get(AIAction, receipt_id).result == {"id": "script"}
    engine.dispose()
