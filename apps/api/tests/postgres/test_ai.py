"""Real PostgreSQL coverage for scoped retrieval and append-only event receipts."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from robopark_api.ai_models import AIChunk, AIDocument, AIEvent
from robopark_api.ai_schemas import DocumentIn
from robopark_api.models import Park, User
from robopark_api.services.ai import knowledge
from robopark_api.services.rbac import get_role_by_slug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def test_real_postgres_knowledge_index_dedupe_and_tombstone(migrated_engine):  # noqa: F811
    with Session(migrated_engine) as db:
        ensure_rbac_catalog(db)
        unique = uuid4().hex
        user = User(
            username="ai-" + unique,
            password_hash="unused",
            role_id=get_role_by_slug(db, "admin").id,
            access_status="approved",
            is_active=True,
        )
        park = Park(name="ai-" + unique, tag="ai-" + unique, is_active=True)
        db.add_all([user, park])
        db.flush()
        value = DocumentIn(
            title="Камера " + unique,
            content="Проверьте кабель камеры " + unique,
            kind="manual",
            state="active",
            park_id=park.id,
            source_ref=unique,
        )
        doc, created = knowledge.add(db, user, value)
        db.commit()
        assert created
        assert knowledge.search(db, user, unique, park_id=park.id)[0]["id"] == doc.id
        assert knowledge.add(db, user, value)[1] is False
        event = AIEvent(key=unique, park_id=park.id, payload={"issue_key": "R-1"}, occurred_at=1)
        db.add(event)
        db.commit()
        assert event.processed is False
        knowledge.remove(db, doc)
        assert (
            db.scalar(
                select(func.count()).select_from(AIChunk).where(AIChunk.document_id == doc.id)
            )
            == 0
        )
        assert knowledge.add(db, user, value)[1] is False
        assert db.get(AIDocument, doc.id).state == "deleted"
        db.rollback()
