import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import VALID_PASSWORD, login_as
from robopark_api.models import AgentDevice, AuthSession, DeviceAuthChallenge
from robopark_api.services import device_auth


def _installation():
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return key, base64.b64encode(public).decode("ascii")


def _claim(client, public_key: str) -> dict:
    response = client.post(
        "/agent-devices/claim-auth",
        json={
            "label": "Passwordless Mac",
            "public_key": public_key,
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _challenge(client, device_id: str):
    response = client.post("/auth/device-auth/challenge", json={"device_id": device_id})
    assert response.status_code == 200, response.text
    return response.json()


def _signature(key, challenge: dict, device_id: str) -> str:
    message = (
        "robopark-device-auth-v1\n"
        f"{challenge['instance_id']}\n{challenge['environment']}\n{device_id}\n"
        f"{challenge['challenge_id']}\n{challenge['nonce']}\n{challenge['expires_at']}"
    ).encode()
    return base64.b64encode(key.sign(message, ec.ECDSA(hashes.SHA256()))).decode("ascii")


def _renew(client, key, challenge: dict, device_id: str):
    return client.post(
        "/auth/device-auth/renew",
        json={
            "device_id": device_id,
            "challenge_id": challenge["challenge_id"],
            "nonce": challenge["nonce"],
            "signature": _signature(key, challenge, device_id),
        },
    )


def test_signed_device_challenge_renews_normal_http_only_session(client, db_session, seed_admin):
    key, public_key = _installation()
    login_as(client, "admin", "secret")
    claimed = _claim(client, public_key)
    client.cookies.clear()

    challenge = _challenge(client, claimed["device_id"])
    renewed = _renew(client, key, challenge, claimed["device_id"])

    assert renewed.status_code == 204
    assert "HttpOnly" in renewed.headers["set-cookie"]
    assert client.get("/auth/me").json()["id"] == seed_admin.id
    session = db_session.scalar(select(AuthSession).where(AuthSession.auth_method == "device"))
    assert session is not None
    assert session.device_id == claimed["device_id"]


def test_device_challenge_rejects_bad_signature_then_consumes_exactly_once(
    client, db_session, seed_admin
):
    key, public_key = _installation()
    other_key, _ = _installation()
    login_as(client, "admin", "secret")
    device_id = _claim(client, public_key)["device_id"]
    client.cookies.clear()
    challenge = _challenge(client, device_id)

    bad = _renew(client, other_key, challenge, device_id)
    good = _renew(client, key, challenge, device_id)
    replay = _renew(client, key, challenge, device_id)

    assert bad.status_code == 401
    assert good.status_code == 204
    assert replay.status_code == 401
    row = db_session.get(DeviceAuthChallenge, challenge["challenge_id"])
    assert row is not None and row.consumed_at is not None


def test_expired_wrong_scope_and_revoked_device_fail_closed(client, db_session, seed_admin):
    first_key, first_public = _installation()
    second_key, second_public = _installation()
    login_as(client, "admin", "secret")
    first = _claim(client, first_public)["device_id"]
    login_as(client, "admin", "secret")
    second = _claim(client, second_public)["device_id"]
    client.cookies.clear()

    challenge = _challenge(client, first)
    wrong_scope = _renew(client, second_key, challenge, second)
    assert wrong_scope.status_code == 401

    row = db_session.get(DeviceAuthChallenge, challenge["challenge_id"])
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert _renew(client, first_key, challenge, first).status_code == 401

    login_as(client, "admin", "secret")
    assert client.delete(f"/agent-devices/{first}").status_code == 204
    client.cookies.clear()
    denied = client.post("/auth/device-auth/challenge", json={"device_id": first})
    assert denied.status_code == 401


def test_owner_state_and_password_change_disable_device_auth(client, db_session, seed_admin):
    _key, public_key = _installation()
    login_as(client, "admin", "secret")
    device_id = _claim(client, public_key)["device_id"]
    device = db_session.get(AgentDevice, device_id)
    assert device is not None and device.device_auth_enabled_at is not None

    seed_admin.must_change_password = True
    db_session.commit()
    client.cookies.clear()
    assert (
        client.post("/auth/device-auth/challenge", json={"device_id": device_id}).status_code == 401
    )

    seed_admin.must_change_password = False
    db_session.commit()
    login_as(client, "admin", "secret")
    changed = client.post(
        "/auth/change-password",
        json={"current_password": "secret", "new_password": VALID_PASSWORD},
    )
    assert changed.status_code == 204, changed.text
    db_session.refresh(device)
    assert device.device_auth_enabled_at is None


def test_explicit_device_logout_revokes_grant_and_device_sessions(client, db_session, seed_admin):
    key, public_key = _installation()
    login_as(client, "admin", "secret")
    device_id = _claim(client, public_key)["device_id"]
    client.cookies.clear()
    challenge = _challenge(client, device_id)
    assert _renew(client, key, challenge, device_id).status_code == 204

    assert client.post("/auth/logout").status_code == 204
    device = db_session.get(AgentDevice, device_id)
    assert device is not None and device.device_auth_enabled_at is None
    assert db_session.scalar(select(AuthSession).where(AuthSession.device_id == device_id)) is None
    assert (
        client.post("/auth/device-auth/challenge", json={"device_id": device_id}).status_code == 401
    )


def test_password_session_logout_revokes_device_bound_during_claim(client, db_session, seed_admin):
    _key, public_key = _installation()
    _other_key, other_public = _installation()
    login_as(client, "admin", "secret")
    device_id = _claim(client, public_key)["device_id"]
    password_session = db_session.scalar(
        select(AuthSession).where(AuthSession.auth_method == "password")
    )
    assert password_session is not None and password_session.device_id == device_id
    rebound = client.post(
        "/agent-devices/claim-auth",
        json={
            "label": "Other Mac",
            "public_key": other_public,
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert rebound.status_code == 403

    assert client.post("/auth/logout").status_code == 204

    device = db_session.get(AgentDevice, device_id)
    assert device is not None and device.device_auth_enabled_at is None
    assert db_session.scalar(select(AuthSession).where(AuthSession.device_id == device_id)) is None
    assert (
        client.post("/auth/device-auth/challenge", json={"device_id": device_id}).status_code == 401
    )


def test_device_session_can_only_reclaim_its_current_key(client, db_session, seed_admin):
    key, public_key = _installation()
    _other_key, other_public = _installation()
    login_as(client, "admin", "secret")
    claimed = _claim(client, public_key)
    client.cookies.clear()
    challenge = _challenge(client, claimed["device_id"])
    assert _renew(client, key, challenge, claimed["device_id"]).status_code == 204

    current = client.post(
        "/agent-devices/claim-auth",
        json={
            "label": "Passwordless Mac",
            "public_key": public_key,
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    second = client.post(
        "/agent-devices/claim-auth",
        json={
            "label": "Second Mac",
            "public_key": other_public,
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert current.status_code == 200
    assert second.status_code == 403
    assert db_session.scalar(select(AgentDevice).where(AgentDevice.public_key_x963 != b""))


def test_claim_auth_requires_recent_password_session(client, db_session, seed_admin):
    _key, public_key = _installation()
    login_as(client, "admin", "secret")
    session = db_session.scalar(select(AuthSession))
    assert session is not None
    session.created_at = datetime.now(UTC) - timedelta(minutes=11)
    db_session.commit()

    response = client.post(
        "/agent-devices/claim-auth",
        json={
            "label": "Stale login Mac",
            "public_key": public_key,
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "fresh_password_auth_required"
    assert db_session.scalar(select(AgentDevice)) is None


def test_legacy_enrollment_does_not_silently_enable_device_auth(client, db_session, seed_admin):
    _key, public_key = _installation()
    login_as(client, "admin", "secret")
    code = client.post("/agent-devices/enrollments", json={"label": "Legacy Mac"}).json()["code"]
    claimed = client.post(
        "/agent-devices/claim",
        json={
            "code": code,
            "label": "Legacy Mac",
            "public_key": public_key,
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert claimed.status_code == 201
    device = db_session.get(AgentDevice, claimed.json()["device_id"])
    assert device is not None and device.device_auth_enabled_at is None
    password_session = db_session.scalar(select(AuthSession))
    assert password_session is not None
    password_session.created_at = datetime.now(UTC) - timedelta(minutes=11)
    db_session.commit()
    existing_payload = {
        "label": "Legacy Mac",
        "public_key": public_key,
        "environment": "production",
        "instance_id": "00000000-0000-0000-0000-000000000000",
    }
    stale_recovery = client.post("/agent-devices/claim-auth", json=existing_payload)
    assert stale_recovery.status_code == 200
    db_session.refresh(device)
    db_session.refresh(password_session)
    assert device.device_auth_enabled_at is None
    assert password_session.device_id is None

    login_as(client, "admin", "secret")
    fresh_recovery = client.post("/agent-devices/claim-auth", json=existing_payload)
    assert fresh_recovery.status_code == 200
    db_session.refresh(device)
    assert device.device_auth_enabled_at is not None
    client.cookies.clear()
    assert (
        client.post(
            "/auth/device-auth/challenge", json={"device_id": claimed.json()["device_id"]}
        ).status_code
        == 200
    )


def test_stale_bound_password_session_can_read_existing_enabled_device(
    client, db_session, seed_admin
):
    _key, public_key = _installation()
    login_as(client, "admin", "secret")
    claimed = _claim(client, public_key)
    session = db_session.scalar(select(AuthSession))
    assert session is not None and session.device_id == claimed["device_id"]
    enabled_at = db_session.get(AgentDevice, claimed["device_id"]).device_auth_enabled_at
    session.created_at = datetime.now(UTC) - timedelta(minutes=11)
    db_session.commit()

    response = client.post(
        "/agent-devices/claim-auth",
        json={
            "label": "Passwordless Mac",
            "public_key": public_key,
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )

    assert response.status_code == 200
    device = db_session.get(AgentDevice, claimed["device_id"])
    assert device is not None and device.device_auth_enabled_at == enabled_at


def test_admin_password_reset_disables_grant(client, db_session, seed_admin, seed_royal):
    _key, public_key = _installation()
    login_as(client, "admin", "secret")
    device_id = _claim(client, public_key)["device_id"]
    login_as(client, "royal", "secret")

    reset = client.patch(
        f"/admin/users/{seed_admin.id}",
        json={"password": VALID_PASSWORD},
    )

    assert reset.status_code == 200, reset.text
    device = db_session.get(AgentDevice, device_id)
    assert device is not None and device.device_auth_enabled_at is None


def test_pending_challenge_cap_consumes_oldest_not_newest(client, db_session, seed_admin):
    _key, public_key = _installation()
    login_as(client, "admin", "secret")
    device_id = _claim(client, public_key)["device_id"]
    client.cookies.clear()

    issued = [_challenge(client, device_id)["challenge_id"] for _ in range(6)]
    rows = {
        row.id: row
        for row in db_session.scalars(
            select(DeviceAuthChallenge).where(DeviceAuthChallenge.device_id == device_id)
        ).all()
    }

    assert rows[issued[0]].consumed_at is not None
    assert all(rows[item].consumed_at is None for item in issued[1:])


def test_device_rate_budget_is_shared_across_independent_database_sessions(
    client, db_engine, db_session, seed_admin
):
    _key, public_key = _installation()
    login_as(client, "admin", "secret")
    device_id = _claim(client, public_key)["device_id"]
    now = datetime.now(UTC)

    for attempt in range(device_auth.DEVICE_REQUEST_LIMIT):
        with Session(db_engine) as independent_worker:
            device_auth.charge_device_request(
                independent_worker,
                device_id,
                now=now + timedelta(milliseconds=attempt),
            )

    with Session(db_engine) as independent_worker, pytest.raises(HTTPException) as denied:
        device_auth.charge_device_request(
            independent_worker,
            device_id,
            now=now + timedelta(milliseconds=device_auth.DEVICE_REQUEST_LIMIT),
        )

    assert denied.value.status_code == 429
    db_session.expire_all()
    device = db_session.get(AgentDevice, device_id)
    assert device is not None
    assert device.device_auth_rate_count == device_auth.DEVICE_REQUEST_LIMIT + 1
