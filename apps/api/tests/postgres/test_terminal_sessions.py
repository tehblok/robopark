"""Concurrent independent PostgreSQL contexts consume each authority only once."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.db import configure_engine
from robopark_api.models import (
    AuthSession,
    PrivilegedCredential,
    PrivilegedReauthorization,
    Role,
    User,
)
from robopark_api.services.rbac_seed import ensure_rbac_catalog
from robopark_api.services.terminal.sessions import (
    TerminalError,
    consume_attach_ticket,
    issue_attach_ticket,
    reserve_session,
)
from robopark_api.terminal_models import TerminalAttachTicket, TerminalSession, TerminalSessionEvent

pytestmark = pytest.mark.postgres


@pytest.fixture
def terminal_db(postgres_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", postgres_database_url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = configure_engine(postgres_database_url)
    now = datetime.now(UTC)
    sid = str(uuid4())
    login = uuid4().hex * 2
    raw = uuid4().hex
    cap = {"capability_revision": "b" * 64, "boot_id": str(uuid4()), "broker_epoch": str(uuid4())}
    with Session(engine) as db:
        ensure_rbac_catalog(db)
        role = db.scalar(select(Role).where(Role.slug == "royal"))
        actor = User(
            username=f"term-{uuid4().hex}",
            password_hash="not-used",
            role_id=role.id,
            access_status="approved",
            is_active=True,
        )
        db.add(actor)
        db.flush()
        owner = actor.id
        db.add_all(
            [
                AuthSession(user_id=owner, token_hash=login, expires_at=now + timedelta(hours=1)),
                PrivilegedCredential(
                    user_id=owner,
                    totp_secret_encrypted="fixture",
                    enrolled_at=now,
                    credential_generation=1,
                ),
                PrivilegedReauthorization(
                    token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    user_id=owner,
                    session_token_hash=login,
                    operation_kind="terminal.open.maintenance",
                    operation_id=sid,
                    capability_revision="b" * 64,
                    credential_generation=1,
                    totp_only=True,
                    expires_at=now + timedelta(seconds=120),
                ),
            ]
        )
        db.commit()
    yield (
        engine,
        {
            "actor_id": owner,
            "auth_session_hash": login,
            "session_id": sid,
            "profile": "maintenance",
            "capability": cap,
            "token": raw,
            "settings": Settings(database_url=postgres_database_url),
        },
    )
    engine.dispose()


def test_concurrent_create_consumes_one_grant(terminal_db):
    engine, args = terminal_db
    barrier = Barrier(2)

    def reserve():
        with Session(engine) as db:
            barrier.wait(timeout=5)
            row, fresh = reserve_session(db, **args)
            descriptor = dict(row.descriptor)
            db.commit()
            return fresh, descriptor

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(fresh for fresh, _ in results) == [False, True]
    assert results[0][1] == results[1][1]
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TerminalSessionEvent)
                .where(
                    TerminalSessionEvent.session_id == args["session_id"],
                    TerminalSessionEvent.event == "reserved",
                )
            )
            == 1
        )


def test_concurrent_attach_consumes_one_ticket(terminal_db):
    engine, args = terminal_db
    with Session(engine) as db:
        row, _ = reserve_session(db, **args)
        ticket = issue_attach_ticket(db, row=row)
        db.commit()
    barrier = Barrier(2)

    def attach():
        with Session(engine) as db:
            barrier.wait(timeout=5)
            try:
                row = consume_attach_ticket(
                    db,
                    raw_ticket=ticket,
                    session_id=args["session_id"],
                    auth_session_hash=args["auth_session_hash"],
                    broker_epoch=args["capability"]["broker_epoch"],
                )
                db.commit()
                return row.attachment_id
            except TerminalError:
                db.rollback()
                return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: attach(), range(2)))
    assert len([value for value in results if value]) == 1


def test_concurrent_ticket_issuance_is_bounded_and_counters_are_bigints(terminal_db):
    engine, args = terminal_db
    with Session(engine) as db:
        row, _ = reserve_session(db, **args)
        row.input_bytes = 3_000_000_000
        row.output_bytes = 4_000_000_000
        db.commit()
    barrier = Barrier(5)

    def issue():
        with Session(engine) as db:
            row = db.get(TerminalSession, args["session_id"])
            barrier.wait(timeout=5)
            try:
                issue_attach_ticket(db, row=row)
                db.commit()
                return True
            except TerminalError:
                db.rollback()
                return False

    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(lambda _: issue(), range(5)))
    assert results.count(True) == 4
    with Session(engine) as db:
        row = db.get(TerminalSession, args["session_id"])
        assert (row.input_bytes, row.output_bytes) == (3_000_000_000, 4_000_000_000)
        assert (
            db.scalar(
                select(func.count())
                .select_from(TerminalAttachTicket)
                .where(TerminalAttachTicket.session_id == args["session_id"])
            )
            == 4
        )
