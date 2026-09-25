import base64
import hashlib
import hmac
import struct

from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import (
    PrivilegedAuthAudit,
    PrivilegedCredential,
    PrivilegedRecoveryCode,
    User,
)
from robopark_api.security import hash_password
from robopark_api.services import rbac


def _totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def test_enrollment_requires_password_totp_encrypts_secret_and_shows_ten_codes_once(
    client, seed_royal, db_session, monkeypatch
):
    login_as(client, "royal", "secret")
    started = client.post("/admin/privileged-auth/enrollment")
    assert started.status_code == 200
    secret = started.json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 3_000.0)

    denied = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "wrong", "code": _totp(secret, 100)},
    )
    assert denied.status_code == 401
    confirmed = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 100)},
    )
    assert confirmed.status_code == 200
    recovery_codes = confirmed.json()["recovery_codes"]
    assert len(recovery_codes) == len(set(recovery_codes)) == 10
    assert all(len(code) >= 20 for code in recovery_codes)

    credential = db_session.get(PrivilegedCredential, seed_royal.id)
    assert credential is not None
    assert secret not in credential.totp_secret_encrypted
    assert len(db_session.scalars(select(PrivilegedRecoveryCode)).all()) == 10
    assert all(
        code not in {row.code_hash for row in db_session.scalars(select(PrivilegedRecoveryCode))}
        for code in recovery_codes
    )
    assert client.post("/admin/privileged-auth/enrollment").status_code == 409


def test_reauthorization_is_session_operation_bound_one_use_and_audited(
    client, seed_royal, db_session, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 6_000.0)
    recovery = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 200)},
    ).json()["recovery_codes"][0]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 6_030.0)

    issued = client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": _totp(secret, 201),
            "operation_kind": "snapshot",
            "operation_id": "snapshot",
        },
    )
    assert issued.status_code == 200
    token = issued.json()["token"]
    assert token not in str(db_session.execute(select(PrivilegedCredential)).all())

    wrong = client.post("/admin/ops/repair", headers={"X-Privileged-Authorization": token})
    assert wrong.status_code == 401
    ok = client.post("/admin/ops/snapshot", headers={"X-Privileged-Authorization": token})
    assert ok.status_code == 200
    assert (
        client.post("/admin/ops/snapshot", headers={"X-Privileged-Authorization": token}).status_code
        == 401
    )

    recovery_auth = client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": recovery,
            "operation_kind": "repair",
            "operation_id": "repair",
        },
    )
    assert recovery_auth.status_code == 200
    assert client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": recovery,
            "operation_kind": "repair",
            "operation_id": "repair-2",
        },
    ).status_code == 401
    audits = db_session.scalars(select(PrivilegedAuthAudit)).all()
    assert {row.outcome for row in audits} >= {"success", "denied"}
    assert all(row.actor_username == "royal" and row.actor_role == "royal" for row in audits)
    assert all(secret not in (row.reason or "") and recovery not in (row.reason or "") for row in audits)


def test_only_enrolled_active_royal_cannot_be_deactivated(
    client, seed_royal, db_session, monkeypatch
):
    second = User(
        username="other-royal",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, rbac.RoleSlug.ROYAL),
        access_status="approved",
        is_active=True,
    )
    db_session.add(second)
    db_session.commit()
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 9_000.0)
    assert client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 300)},
    ).status_code == 200

    login_as(client, "other-royal", "secret")
    response = client.patch(f"/admin/users/{seed_royal.id}", json={"is_active": False})
    assert response.status_code == 400
    assert response.json()["detail"] == "cannot_disable_last_royal"
