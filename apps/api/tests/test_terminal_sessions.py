import hashlib
import importlib.util
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from robopark_api.config import Settings
from robopark_api.models import (
    AuthSession,
    PrivilegedCredential,
    PrivilegedReauthorization,
    PrivilegedRecoveryCode,
)
from robopark_api.terminal_models import TerminalAttachTicket
from robopark_api.terminal_schemas import SessionOut


def terminal():
    assert importlib.util.find_spec("robopark_api.services.terminal.sessions"), (
        "terminal sessions missing"
    )
    from robopark_api.services.terminal import sessions

    return sessions


def setup_grant(db, actor, *, totp_only=True, profile="maintenance"):
    now = datetime.now(UTC)
    sid = str(uuid4())
    login = "a" * 64
    raw = "test-grant"
    db.add(AuthSession(user_id=actor.id, token_hash=login, expires_at=now + timedelta(hours=1)))
    db.add(
        PrivilegedCredential(
            user_id=actor.id,
            totp_secret_encrypted="fixture",
            enrolled_at=now,
            credential_generation=1,
        )
    )
    db.add(
        PrivilegedReauthorization(
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            user_id=actor.id,
            session_token_hash=login,
            operation_kind=f"terminal.open.{profile}",
            operation_id=sid,
            capability_revision="b" * 64,
            credential_generation=1,
            expires_at=now + timedelta(seconds=120),
            totp_only=totp_only,
        )
    )
    db.commit()
    return sid, login, raw


def test_terminal_grant_requires_totp_provenance(db_session, seed_royal):
    service = terminal()
    sid, login, raw = setup_grant(db_session, seed_royal, totp_only=False)
    with pytest.raises(service.TerminalError, match="reauthorization"):
        service.reserve_session(
            db_session,
            actor_id=seed_royal.id,
            auth_session_hash=login,
            session_id=sid,
            profile="maintenance",
            capability={
                "capability_revision": "b" * 64,
                "boot_id": str(uuid4()),
                "broker_epoch": str(uuid4()),
            },
            token=raw,
            settings=Settings(),
        )


def test_terminal_reauthorization_rejects_recovery_code_without_consuming_it(
    client, db_session, seed_royal, test_settings, monkeypatch
):
    from conftest import _test_totp, login_as
    from robopark_api.services import privileged_auth

    login_as(client, "royal", "secret")
    monkeypatch.setattr(privileged_auth, "_unix_time", lambda: 3_000.0)
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    recovery = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _test_totp(secret, 100)},
    ).json()["recovery_codes"][0]
    monkeypatch.setattr(
        "robopark_api.routers.privileged_auth.validate_grant_context",
        lambda *_args, **_kwargs: None,
    )
    test_settings.terminal_allowed_origins = "http://localhost:5173"
    test_settings.terminal_allow_loopback_http = True

    response = client.post(
        "/admin/privileged-auth/reauthorize",
        headers={"origin": "http://localhost:5173", "host": "localhost:5173"},
        json={
            "password": "secret",
            "code": recovery,
            "operation_kind": "terminal.open.root",
            "operation_id": str(uuid4()),
            "capability_revision": "b" * 64,
        },
    )

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "invalid_credentials"
    db_session.expire_all()
    assert all(row.used_at is None for row in db_session.scalars(select(PrivilegedRecoveryCode)))


def test_reservation_and_grant_consumption_are_one_transaction(db_session, seed_royal):
    service = terminal()
    sid, login, raw = setup_grant(db_session, seed_royal)
    cap = {"capability_revision": "b" * 64, "boot_id": str(uuid4()), "broker_epoch": str(uuid4())}
    first, fresh = service.reserve_session(
        db_session,
        actor_id=seed_royal.id,
        auth_session_hash=login,
        session_id=sid,
        profile="maintenance",
        capability=cap,
        token=raw,
        settings=Settings(),
    )
    assert fresh and first.state == "starting"
    db_session.rollback()
    grant = db_session.query(PrivilegedReauthorization).one()
    assert grant.used_at is None
    first, fresh = service.reserve_session(
        db_session,
        actor_id=seed_royal.id,
        auth_session_hash=login,
        session_id=sid,
        profile="maintenance",
        capability=cap,
        token=raw,
        settings=Settings(),
    )
    db_session.commit()
    second, fresh = service.reserve_session(
        db_session,
        actor_id=seed_royal.id,
        auth_session_hash=login,
        session_id=sid,
        profile="maintenance",
        capability=cap,
        token=raw,
        settings=Settings(),
    )
    assert not fresh and first.descriptor == second.descriptor
    with pytest.raises(service.TerminalError, match="conflict"):
        service.reserve_session(
            db_session,
            actor_id=seed_royal.id,
            auth_session_hash=login,
            session_id=sid,
            profile="root",
            capability=cap,
            token=raw,
            settings=Settings(),
        )


def test_terminal_authorization_checks_current_login_and_role(db_session, seed_royal):
    service = terminal()
    sid, login, raw = setup_grant(db_session, seed_royal)
    assert (
        service.authorize_terminal(
            db_session, auth_session_hash=login, owner_id=seed_royal.id, settings=Settings()
        ).id
        == seed_royal.id
    )
    seed_royal.is_active = False
    db_session.commit()
    with pytest.raises(service.TerminalError):
        service.authorize_terminal(
            db_session, auth_session_hash=login, owner_id=seed_royal.id, settings=Settings()
        )


def test_password_change_revokes_terminal_even_current_login(client, seed_royal, db_session):
    from conftest import VALID_PASSWORD, login_as
    from robopark_api.security import hash_session_token
    from robopark_api.terminal_models import TerminalSession

    login_as(client, "royal", "secret")
    now = datetime.now(UTC)
    row = TerminalSession(
        id=str(uuid4()),
        owner_id=seed_royal.id,
        auth_session_hash=hash_session_token(client.cookies.get("robopark_session")),
        profile="maintenance",
        state="active",
        credential_generation=1,
        broker_epoch=str(uuid4()),
        descriptor={},
        expires_at=now + timedelta(hours=1),
    )
    db_session.add(row)
    db_session.commit()
    response = client.post(
        "/auth/change-password", json={"current_password": "secret", "new_password": VALID_PASSWORD}
    )
    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(TerminalSession, row.id).state == "ended"


def test_attach_ticket_issuance_is_bounded(db_session, seed_royal):
    service = terminal()
    sid, login, raw = setup_grant(db_session, seed_royal)
    cap = {"capability_revision": "b" * 64, "boot_id": str(uuid4()), "broker_epoch": str(uuid4())}
    row, _ = service.reserve_session(
        db_session,
        actor_id=seed_royal.id,
        auth_session_hash=login,
        session_id=sid,
        profile="maintenance",
        capability=cap,
        token=raw,
        settings=Settings(),
    )
    for _ in range(4):
        service.issue_attach_ticket(db_session, row=row)
    with pytest.raises(service.TerminalError, match="ticket_limit"):
        service.issue_attach_ticket(db_session, row=row)
    db_session.commit()
    assert len(db_session.scalars(select(TerminalAttachTicket)).all()) == 4


def test_host_operation_admission_rejects_fresh_active_terminal(monkeypatch):
    from robopark_api.routers import admin_ops

    monkeypatch.setattr(
        admin_ops, "terminal_capabilities", lambda _settings: {"active_sessions": 1}
    )
    with pytest.raises(HTTPException) as error:
        admin_ops._require_no_active_terminal(Settings())
    assert error.value.status_code == 409
    assert error.value.detail == "terminal_active"


def test_terminal_session_response_makes_sqlite_timestamp_explicit_utc():
    response = SessionOut.model_validate(
        {
            "id": str(uuid4()),
            "profile": "maintenance",
            "state": "detached",
            "expires_at": datetime(2026, 10, 2, 12, 0),
            "broker_epoch": str(uuid4()),
            "termination_reason": None,
        }
    )
    assert response.expires_at.tzinfo is UTC


@pytest.mark.parametrize("initial_state", ["ended", "detached"])
def test_close_is_idempotent_when_broker_record_is_missing(
    db_session, seed_royal, monkeypatch, initial_state
):
    import asyncio
    from types import SimpleNamespace

    from fastapi import Response

    from robopark_api.routers import admin_terminal
    from robopark_api.services.terminal.authorization import TerminalError
    from robopark_api.terminal_models import TerminalSession

    session_id, login, _ = setup_grant(db_session, seed_royal)
    row = TerminalSession(
        id=session_id,
        owner_id=seed_royal.id,
        auth_session_hash=login,
        profile="maintenance",
        state=initial_state,
        credential_generation=1,
        broker_epoch=str(uuid4()),
        descriptor={},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(admin_terminal, "_access", lambda *args, **kwargs: login)
    calls = []

    async def missing(identity):
        calls.append(identity)
        raise TerminalError("terminal_session_missing")

    monkeypatch.setattr(
        admin_terminal, "BrokerClient", lambda _: SimpleNamespace(terminate=missing)
    )

    async def close_twice():
        from uuid import UUID

        for _ in range(2):
            result = await admin_terminal.terminate(
                UUID(session_id), object(), Response(), seed_royal, db_session, Settings()
            )
            assert result.state == "ended"

    asyncio.run(close_twice())
    assert len(calls) == (0 if initial_state == "ended" else 1)
