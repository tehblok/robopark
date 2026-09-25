import base64
import hashlib
import hmac
import struct

import pytest
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import (
    PrivilegedAuthAudit,
    PrivilegedCredential,
    PrivilegedReauthorization,
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


def test_recovery_reset_survives_key_loss_and_replaces_all_credentials(
    client, seed_royal, db_session, test_settings, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 12_000.0)
    original_codes = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 400)},
    ).json()["recovery_codes"]
    old_ciphertext = db_session.get(PrivilegedCredential, seed_royal.id).totp_secret_encrypted
    object.__setattr__(test_settings, "secret_key", "rotated-test-key")

    denied = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": "not-a-recovery-code"},
    )
    assert denied.status_code == 401
    assert db_session.get(PrivilegedCredential, seed_royal.id).totp_secret_encrypted == old_ciphertext

    reset = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": original_codes[0]},
    )
    assert reset.status_code == 200, reset.text
    payload = reset.json()
    assert payload["secret"] != secret
    assert len(payload["recovery_codes"]) == len(set(payload["recovery_codes"])) == 10
    rows = db_session.scalars(select(PrivilegedRecoveryCode)).all()
    assert len([row for row in rows if row.used_at is None]) == 10
    assert {row.hash_version for row in rows} == {"scrypt-v1"}
    assert db_session.get(PrivilegedCredential, seed_royal.id).totp_secret_encrypted != old_ciphertext


def test_key_loss_is_controlled_and_recovery_is_checked_before_totp_decryption(
    client, seed_royal, test_settings, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 15_000.0)
    recovery = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 500)},
    ).json()["recovery_codes"][0]
    object.__setattr__(test_settings, "secret_key", "lost-key-replacement")

    recovered = client.post(
        "/admin/privileged-auth/reauthorize",
        json={"password": "secret", "code": recovery, "operation_kind": "repair", "operation_id": "repair"},
    )
    assert recovered.status_code == 200
    totp = client.post(
        "/admin/privileged-auth/reauthorize",
        json={"password": "secret", "code": "123456", "operation_kind": "repair", "operation_id": "repair-2"},
    )
    assert totp.status_code == 409
    assert totp.json()["detail"] == "credential_unavailable"


def test_missing_key_during_confirmation_is_controlled_and_audited(
    client, seed_royal, db_session, test_settings, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 15_300.0)
    object.__setattr__(test_settings, "secret_key", "")

    response = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 510)},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "credential_unavailable"
    audit = db_session.scalars(
        select(PrivilegedAuthAudit).where(
            PrivilegedAuthAudit.action == "privileged.enrollment.confirm"
        )
    ).one()
    assert audit.outcome == "denied"
    assert audit.reason == "credential_unavailable"


def test_missing_key_during_enrollment_begin_is_controlled_and_audited(
    client, seed_royal, db_session, test_settings
):
    login_as(client, "royal", "secret")
    object.__setattr__(test_settings, "secret_key", "")

    response = client.post("/admin/privileged-auth/enrollment")

    assert response.status_code == 409
    assert response.json()["detail"] == "secret_key_required"
    audit = db_session.scalars(
        select(PrivilegedAuthAudit).where(
            PrivilegedAuthAudit.action == "privileged.enrollment.begin"
        )
    ).one()
    assert audit.outcome == "denied"
    assert audit.reason == "secret_key_required"


def test_legacy_recovery_hashes_require_totp_rotation_before_key_change(
    client, seed_royal, db_session, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 16_500.0)
    codes = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 550)},
    ).json()["recovery_codes"]
    for row in db_session.scalars(select(PrivilegedRecoveryCode)):
        row.hash_version = "legacy-hmac-v1"
    db_session.commit()

    unsupported = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": codes[0]},
    )
    assert unsupported.status_code == 409
    assert unsupported.json()["detail"] == "recovery_hash_unsupported"

    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 16_530.0)
    rotated = client.post(
        "/admin/privileged-auth/recovery/rotate",
        json={"password": "secret", "code": _totp(secret, 551)},
    )
    assert rotated.status_code == 200
    assert len(rotated.json()["recovery_codes"]) == 10
    assert {
        row.hash_version
        for row in db_session.scalars(
            select(PrivilegedRecoveryCode).where(PrivilegedRecoveryCode.used_at.is_(None))
        )
    } == {"scrypt-v1"}


def test_audit_insert_failure_rolls_back_enrollment_grant_consumption_and_throttle(
    client, seed_royal, db_session, test_settings, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 18_000.0)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("robopark_api.services.privileged_auth._add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(
            "/admin/privileged-auth/enrollment/confirm",
            json={"password": "secret", "code": _totp(secret, 600)},
        )
    db_session.expire_all()
    assert db_session.get(PrivilegedCredential, seed_royal.id).enrolled_at is None
    assert db_session.query(PrivilegedRecoveryCode).count() == 0

    monkeypatch.undo()
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 18_000.0)
    codes = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 600)},
    ).json()["recovery_codes"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 18_030.0)
    monkeypatch.setattr("robopark_api.services.privileged_auth._add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(
            "/admin/privileged-auth/reauthorize",
            json={"password": "secret", "code": _totp(secret, 601), "operation_kind": "snapshot", "operation_id": "snapshot"},
        )
    db_session.expire_all()
    assert db_session.query(PrivilegedReauthorization).count() == 0
    assert db_session.get(PrivilegedCredential, seed_royal.id).last_totp_counter == 600

    monkeypatch.undo()
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 18_030.0)
    token = client.post(
        "/admin/privileged-auth/reauthorize",
        json={"password": "secret", "code": _totp(secret, 601), "operation_kind": "snapshot", "operation_id": "snapshot"},
    ).json()["token"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post("/admin/ops/snapshot", headers={"X-Privileged-Authorization": token})
    db_session.expire_all()
    assert db_session.query(PrivilegedReauthorization).one().used_at is None

    monkeypatch.undo()
    monkeypatch.setattr("robopark_api.services.privileged_auth._add_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.post(
            "/admin/privileged-auth/reauthorize",
            json={"password": "wrong", "code": codes[0], "operation_kind": "repair", "operation_id": "repair"},
        )
    from robopark_api.models import AuthThrottleState

    db_session.expire_all()
    assert db_session.query(AuthThrottleState).count() == 0
