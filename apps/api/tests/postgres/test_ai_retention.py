"""Verify retention query/index semantics on the production DB dialect."""

from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from robopark_api.ai_models import AIEvent, AIRun
from robopark_api.models import Park
from robopark_api.services.ai import learning

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def test_real_postgres_retention_preserves_inflight_payloads(migrated_engine):  # noqa: F811
    with Session(migrated_engine) as db:
        park = Park(name="Retention", tag=uuid4().hex)
        db.add(park)
        db.flush()
        prefix = uuid4().hex
        done = AIEvent(
            key=prefix + "-done",
            park_id=park.id,
            processed=True,
            occurred_at=1,
            payload={"comment": "old repair"},
        )
        pending = AIEvent(
            key=prefix + "-pending",
            park_id=park.id,
            processed=True,
            occurred_at=1,
            payload={"comment": "required input"},
        )
        db.add_all([done, pending])
        db.flush()
        db.add(AIRun(automation_id=prefix, event_key=pending.key, revision=1, state="queued"))
        db.flush()
        assert learning.purge_event_payloads(db, before=100) == 1
        db.flush()
        assert done.payload == {} and not done.payload_retained
        assert pending.payload == {"comment": "required input"}
        assert learning.purge_event_payloads(db, before=100) == 0
    indexes = {x["name"] for x in inspect(migrated_engine).get_indexes("ai_events")}
    assert "ix_ai_events_retention" in indexes
