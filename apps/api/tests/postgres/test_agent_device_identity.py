"""PostgreSQL enforces one immutable owner boundary per Mac installation key."""

from __future__ import annotations

import base64
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException, Request, Response
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.db import configure_engine
from robopark_api.models import AccessStatus, AgentDevice, AgentEnrollment, Role, User
from robopark_api.routers.agent_devices import (
    AuthenticatedClaimIn,
    ClaimIn,
    _code_hash,
    _new_code,
    claim_authenticated,
)
from robopark_api.routers.agent_devices import (
    claim as claim_with_code,
)
from robopark_api.security import hash_password
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

pytestmark = pytest.mark.postgres


def test_concurrent_first_login_keeps_one_installation_owner(
    postgres_database_url: str, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", postgres_database_url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = configure_engine(postgres_database_url)
    installation_key = b"\x04" + uuid4().bytes * 4
    instance_id = str(uuid4())
    try:
        with Session(engine) as db:
            ensure_rbac_catalog(db)
            role_id = db.scalar(select(Role.id).where(Role.slug == RoleSlug.OPERATOR))
            assert role_id is not None
            owners = []
            for _ in range(2):
                user = User(
                    username=f"agent-owner-{uuid4().hex[:12]}",
                    password_hash=hash_password(uuid4().hex),
                    role_id=role_id,
                    access_status=AccessStatus.approved.value,
                    is_active=True,
                )
                db.add(user)
                db.flush()
                owners.append(user.id)
            db.commit()

        ready = Barrier(2)

        def claim(owner_id: int) -> str:
            with Session(engine) as db:
                ready.wait(timeout=5)
                db.add(
                    AgentDevice(
                        owner_user_id=owner_id,
                        label="Concurrent Mac",
                        public_key_x963=installation_key,
                        environment="test",
                        instance_id=instance_id,
                        approved_by_user_id=owner_id,
                    )
                )
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    return "conflict"
                return "claimed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(claim, owners))

        assert sorted(outcomes) == ["claimed", "conflict"]
        with Session(engine) as db:
            devices = list(
                db.scalars(
                    select(AgentDevice).where(
                        AgentDevice.public_key_x963 == installation_key,
                        AgentDevice.environment == "test",
                        AgentDevice.instance_id == instance_id,
                    )
                )
            )
            assert len(devices) == 1
            assert devices[0].owner_user_id in owners
    finally:
        engine.dispose()


@pytest.mark.parametrize("second_uses_code", [False, True])
def test_parallel_claims_cannot_exceed_owner_limit(
    postgres_database_url, monkeypatch, second_uses_code
):
    monkeypatch.setenv("DATABASE_URL", postgres_database_url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = configure_engine(postgres_database_url)
    instance_id = uuid4()
    settings = Settings(
        _env_file=None,
        database_url=postgres_database_url,
        robopark_environment="test",
        robopark_instance_id=instance_id,
        agent_device_max_per_user=1,
        secret_key="postgres-enrollment-test",
    )
    try:
        with Session(engine) as db:
            ensure_rbac_catalog(db)
            role_id = db.scalar(select(Role.id).where(Role.slug == RoleSlug.OPERATOR))
            owner = User(
                username=f"agent-limit-{uuid4().hex[:12]}",
                password_hash=hash_password(uuid4().hex),
                role_id=role_id,
                access_status=AccessStatus.approved.value,
                is_active=True,
            )
            db.add(owner)
            db.commit()
            owner_id = owner.id
            code = _new_code()
            db.add(
                AgentEnrollment(
                    owner_user_id=owner_id,
                    code_hash=_code_hash(code, settings),
                    label="Mac",
                    environment="test",
                    instance_id=str(instance_id),
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
            db.commit()

        # Simulate slow INSERT storage so concurrent count/insert requests overlap.
        @event.listens_for(engine, "before_cursor_execute")
        def slow_device_insert(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("INSERT INTO AGENT_DEVICES"):
                time.sleep(0.2)

        ready = Barrier(2)
        keys = [
            base64.b64encode(
                ec.generate_private_key(ec.SECP256R1())
                .public_key()
                .public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
            ).decode()
            for _ in range(2)
        ]

        def claim(index_and_key):
            index, public_key = index_and_key
            with Session(engine) as db:
                user = db.get(User, owner_id)
                payload = AuthenticatedClaimIn(
                    label="Concurrent Mac",
                    public_key=public_key,
                    environment="test",
                    instance_id=instance_id,
                )
                ready.wait(timeout=5)
                try:
                    if second_uses_code and index == 1:
                        return claim_with_code(
                            Request({"type": "http", "client": ("127.0.0.1", 1234), "headers": []}),
                            ClaimIn(code=code, **payload.model_dump()),
                            Response(),
                            db,
                            settings,
                        )
                    return claim_authenticated(payload, Response(), user, db, settings)
                except HTTPException as error:
                    db.rollback()
                    return error.detail

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(claim, enumerate(keys)))
        assert len([result for result in outcomes if isinstance(result, dict)]) == 1
        assert outcomes.count("device_limit") == 1
        with Session(engine) as db:
            devices = list(
                db.scalars(select(AgentDevice).where(AgentDevice.owner_user_id == owner_id))
            )
            assert len(devices) == 1
            # Repeated login with the enrolled key remains idempotent at the limit.
            enrolled_key = base64.b64encode(devices[0].public_key_x963).decode()
            repeated = claim_authenticated(
                AuthenticatedClaimIn(
                    label="Mac",
                    public_key=enrolled_key,
                    environment="test",
                    instance_id=instance_id,
                ),
                Response(),
                db.get(User, owner_id),
                db,
                settings,
            )
            assert repeated["device_id"] == devices[0].id
    finally:
        engine.dispose()
