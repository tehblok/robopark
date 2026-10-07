"""Concurrency contracts for the single native Telegram delivery ledger."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from robopark_api.models import NativeBotDelivery, NativeBotJob, Park, User
from robopark_api.native_telegram_schemas import BotJobCreate, BotJobUpdate
from robopark_api.services import native_telegram as service
from robopark_api.services.rbac import get_role_by_slug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.fixture
def native_job(migrated_engine):  # noqa: F811
    with Session(migrated_engine) as db:
        ensure_rbac_catalog(db)
        unique = uuid4().hex
        user = User(
            username=f"telegram-{unique}",
            password_hash="unused",
            role_id=get_role_by_slug(db, "royal").id,
            is_active=True,
            access_status="approved",
        )
        park = Park(name=unique, tag=unique, timezone="UTC", chat_id=-1001234567890)
        db.add_all([user, park])
        db.commit()
        job = service.create_job(
            db,
            user,
            BotJobCreate(
                park_id=park.id,
                kind="text",
                title="Native concurrency test",
                schedule="daily",
                time="12:00",
                weekdays=list(range(7)),
                text="No real Telegram transport is used",
            ),
        )
        ids = user.id, park.id, job.id
    try:
        yield ids
    finally:
        with Session(migrated_engine) as db:
            db.execute(delete(NativeBotDelivery).where(NativeBotDelivery.job_id == ids[2]))
            db.execute(delete(NativeBotJob).where(NativeBotJob.id == ids[2]))
            db.execute(delete(Park).where(Park.id == ids[1]))
            db.execute(delete(User).where(User.id == ids[0]))
            db.commit()


def test_parallel_native_job_edits_accept_only_one_revision(migrated_engine, native_job):  # noqa: F811
    actor_id, _park_id, job_id = native_job
    ready = Barrier(2)

    def edit(index):
        with Session(migrated_engine) as db:
            actor, job = db.get(User, actor_id), db.get(NativeBotJob, job_id)
            payload = BotJobUpdate.model_validate(
                {**service.job_out(job).model_dump(), "title": f"Concurrent edit {index}"}
            )
            ready.wait(timeout=5)
            try:
                service.update_job(db, actor, job_id, payload, payload.revision)
                return 200
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(edit, range(2))) == [200, 409]
    with Session(migrated_engine) as db:
        assert db.get(NativeBotJob, job_id).revision == 2


def test_parallel_pollers_obtain_one_delivery_lease(migrated_engine, native_job, monkeypatch):  # noqa: F811
    _actor_id, _park_id, job_id = native_job
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    with Session(migrated_engine) as db:
        job = db.get(NativeBotJob, job_id)
        job.enabled = True
        job.updated_at = now - timedelta(days=1)
        db.commit()
    ready = Barrier(2)

    def claim(_):
        with Session(migrated_engine) as db:
            ready.wait(timeout=5)
            return service.claim(db, 10)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, range(2)))
    own_claims = [item for batch in claims for item in batch if item["job"].id == job_id]
    assert len(own_claims) == 1
    with Session(migrated_engine) as db:
        receipts = list(
            db.scalars(select(NativeBotDelivery).where(NativeBotDelivery.job_id == job_id))
        )
        assert len(receipts) == 1
        assert receipts[0].state == "preparing"


def test_parallel_link_redeems_code_only_once(migrated_engine, native_job):  # noqa: F811
    actor_id, _park_id, _job_id = native_job
    with Session(migrated_engine) as db:
        code, _expires = service.issue_link_code(db, db.get(User, actor_id))
    ready = Barrier(2)

    def link(telegram_id):
        with Session(migrated_engine) as db:
            ready.wait(timeout=5)
            try:
                service.consume_link_code(db, code, telegram_id)
                return 200
            except HTTPException as error:
                return error.status_code

    # IDs are generated only for the disposable PostgreSQL instance.
    start = int(uuid4().hex[:10], 16)
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(link, [start, start + 1])) == [200, 409]
