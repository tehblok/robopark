"""Owner TOTP enrollment works against the migrated PostgreSQL schema."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from robopark_api import main
from robopark_api.config import Settings, get_settings
from robopark_api.db import configure_engine, get_db
from robopark_api.models import (
    AccessStatus,
    PrivilegedCredential,
    PrivilegedRecoveryCode,
    Role,
    User,
)
from robopark_api.security import hash_password
from robopark_api.services import privileged_auth
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.rbac_seed import ensure_rbac_catalog

pytestmark = pytest.mark.postgres


def _independent_totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1_000_000:06d}"


def test_owner_enrolls_totp_and_recovery_codes_on_clean_postgres(
    postgres_database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DATABASE_URL", postgres_database_url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = configure_engine(postgres_database_url)
    username = f"owner-{uuid4().hex[:12]}"
    password = secrets.token_urlsafe(32)
    try:
        with Session(engine) as db:
            ensure_rbac_catalog(db)
            role_id = db.scalar(select(Role.id).where(Role.slug == RoleSlug.ROYAL))
            assert role_id is not None
            owner = User(
                username=username,
                password_hash=hash_password(password),
                role_id=role_id,
                access_status=AccessStatus.approved.value,
                is_active=True,
            )
            db.add(owner)
            db.commit()
            owner_id = owner.id

        settings = Settings(
            _env_file=None,
            database_url=postgres_database_url,
            secret_key=secrets.token_urlsafe(48),
            seed_username=None,
            seed_password=None,
            ops_dir=str(tmp_path / "ops"),
            ops_apply_root=str(tmp_path / "apply"),
        )
        factory = sessionmaker(bind=engine, future=True)
        monkeypatch.setattr(main, "SessionLocal", factory)
        monkeypatch.setattr(main, "get_settings", lambda: settings)
        monkeypatch.setattr(privileged_auth, "_unix_time", lambda: 30_000.0)
        app = main.create_app()

        def database_override():
            with factory() as db:
                yield db

        app.dependency_overrides[get_db] = database_override
        app.dependency_overrides[get_settings] = lambda: settings

        with TestClient(app) as client:
            assert (
                client.post(
                    "/auth/login", json={"username": username, "password": password}
                ).status_code
                == 204
            )
            assert client.get("/admin/privileged-auth/status").json() == {"enrolled": False}
            started = client.post("/admin/privileged-auth/enrollment")
            assert started.status_code == 200
            secret = started.json()["secret"]
            confirmed = client.post(
                "/admin/privileged-auth/enrollment/confirm",
                json={"password": password, "code": _independent_totp(secret, 1000)},
            )
            assert confirmed.status_code == 200
            recovery_codes = confirmed.json()["recovery_codes"]
            assert len(recovery_codes) == len(set(recovery_codes)) == 10
            assert client.get("/admin/privileged-auth/status").json() == {"enrolled": True}

        with Session(engine) as db:
            credential = db.get(PrivilegedCredential, owner_id)
            assert credential is not None
            assert credential.totp_secret_encrypted.startswith("enc:v1:")
            assert secret not in credential.totp_secret_encrypted
            stored_codes = list(
                db.scalars(
                    select(PrivilegedRecoveryCode).where(PrivilegedRecoveryCode.user_id == owner_id)
                )
            )
            assert len(stored_codes) == 10
            assert set(recovery_codes).isdisjoint({row.code_hash for row in stored_codes})
    finally:
        engine.dispose()
