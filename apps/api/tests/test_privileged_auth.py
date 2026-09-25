import base64
import hashlib
import hmac
import struct
import threading
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.models import (
    PrivilegedAuthAudit,
    PrivilegedCredential,
    PrivilegedReauthorization,
    PrivilegedRecoveryCode,
    User,
)
from robopark_api.security import hash_password
from robopark_api.services import privileged_auth, rbac
from robopark_api.services.login_throttle import LoginThrottle


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


def test_recovery_reset_begin_is_loss_safe_replaceable_and_secret_stays_encrypted(
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

    first = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": original_codes[0]},
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["secret"] != secret
    assert payload["pending_id"]
    assert len(payload["recovery_codes"]) == len(set(payload["recovery_codes"])) == 10
    assert db_session.get(PrivilegedCredential, seed_royal.id).totp_secret_encrypted == old_ciphertext
    rows = db_session.scalars(select(PrivilegedRecoveryCode)).all()
    assert len([row for row in rows if row.used_at is None]) == 10
    assert {row.hash_version for row in rows} == {"scrypt-v1"}
    pending_ciphertext = db_session.execute(
        text(
            "SELECT totp_secret_encrypted FROM privileged_recovery_resets "
            "WHERE id = :pending_id"
        ),
        {"pending_id": payload["pending_id"]},
    ).scalar_one()
    assert payload["secret"] not in pending_ciphertext
    assert db_session.execute(
        text(
            "SELECT count(*) FROM privileged_recovery_reset_codes "
            "WHERE reset_id = :pending_id"
        ),
        {"pending_id": payload["pending_id"]},
    ).scalar_one() == 10
    audit_text = " ".join(
        str(value)
        for audit in db_session.scalars(select(PrivilegedAuthAudit))
        for value in vars(audit).values()
    )
    assert payload["secret"] not in audit_text
    assert all(code not in audit_text for code in payload["recovery_codes"])

    second = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": original_codes[1]},
    )
    assert second.status_code == 200
    assert second.json()["pending_id"] != payload["pending_id"]
    stale = client.post(
        "/admin/privileged-auth/recovery/reset/confirm",
        json={"pending_id": payload["pending_id"], "code": _totp(payload["secret"], 400)},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == "pending_reset_invalid"

    # Losing either begin response leaves both selected old codes usable.
    recovered = client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": original_codes[0],
            "operation_kind": "repair",
            "operation_id": "after-dropped-reset",
        },
    )
    assert recovered.status_code == 200


def test_recovery_reset_confirm_retries_and_expiry_preserve_confirmed_credentials(
    client, seed_royal, db_session, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    clock = {"value": 21_000.0}
    monkeypatch.setattr(
        "robopark_api.services.privileged_auth._unix_time", lambda: clock["value"]
    )
    old_codes = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 700)},
    ).json()["recovery_codes"]
    old_credential = db_session.get(PrivilegedCredential, seed_royal.id)
    old_ciphertext = old_credential.totp_secret_encrypted
    assert old_credential.credential_generation == 1

    begun = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": old_codes[0]},
    ).json()
    wrong = client.post(
        "/admin/privileged-auth/recovery/reset/confirm",
        json={"pending_id": begun["pending_id"], "code": "000000"},
    )
    assert wrong.status_code == 401
    assert db_session.get(PrivilegedCredential, seed_royal.id).totp_secret_encrypted == old_ciphertext

    clock["value"] += 30
    assert client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": _totp(secret, 701),
            "operation_kind": "repair",
            "operation_id": "pending-reset-does-not-replace-totp",
        },
    ).status_code == 200

    confirmed = client.post(
        "/admin/privileged-auth/recovery/reset/confirm",
        json={"pending_id": begun["pending_id"], "code": _totp(begun["secret"], 701)},
    )
    assert confirmed.status_code == 204
    db_session.expire_all()
    credential = db_session.get(PrivilegedCredential, seed_royal.id)
    assert credential.totp_secret_encrypted != old_ciphertext
    assert credential.credential_generation == 2
    assert len(
        db_session.scalars(
            select(PrivilegedRecoveryCode).where(PrivilegedRecoveryCode.used_at.is_(None))
        ).all()
    ) == 10

    clock["value"] += 30
    expiring = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": begun["recovery_codes"][0]},
    ).json()
    confirmed_ciphertext = credential.totp_secret_encrypted
    clock["value"] += 601
    expired = client.post(
        "/admin/privileged-auth/recovery/reset/confirm",
        json={
            "pending_id": expiring["pending_id"],
            "code": _totp(expiring["secret"], int(clock["value"] // 30)),
        },
    )
    assert expired.status_code == 410
    assert expired.json()["detail"] == "pending_reset_expired"
    db_session.expire_all()
    assert db_session.get(PrivilegedCredential, seed_royal.id).totp_secret_encrypted == confirmed_ciphertext
    restarted = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": begun["recovery_codes"][0]},
    )
    assert restarted.status_code == 200


def test_confirmed_reset_invalidates_old_snapshot_grant_before_any_side_effect(
    client, seed_royal, db_session, test_settings, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    clock = {"counter": 800}
    monkeypatch.setattr(
        "robopark_api.services.privileged_auth._unix_time",
        lambda: float(clock["counter"] * 30),
    )
    old_codes = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 800)},
    ).json()["recovery_codes"]
    clock["counter"] += 1
    old_token = client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": _totp(secret, 801),
            "operation_kind": "snapshot",
            "operation_id": "snapshot",
        },
    ).json()["token"]
    grant = db_session.scalar(
        select(PrivilegedReauthorization).where(
            PrivilegedReauthorization.token_hash
            == hashlib.sha256(old_token.encode()).hexdigest()
        )
    )
    assert grant.credential_generation == 1
    begun = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": old_codes[0]},
    ).json()
    assert client.post(
        "/admin/privileged-auth/recovery/reset/confirm",
        json={"pending_id": begun["pending_id"], "code": _totp(begun["secret"], 801)},
    ).status_code == 204

    ops_root = Path(test_settings.ops_dir)
    denied = client.post(
        "/admin/ops/snapshot", headers={"X-Privileged-Authorization": old_token}
    )
    assert denied.status_code == 401
    assert db_session.scalars(
        select(PrivilegedAuthAudit).where(
            PrivilegedAuthAudit.reason == "credential_generation_mismatch"
        )
    ).first()
    assert not ops_root.exists() or not [
        path for path in ops_root.rglob("*") if path.is_file() and path.name != "begin.lock"
    ]
    clock["counter"] += 1
    new_token = client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": _totp(begun["secret"], 802),
            "operation_kind": "snapshot",
            "operation_id": "snapshot",
        },
    ).json()["token"]
    assert client.post(
        "/admin/ops/snapshot", headers={"X-Privileged-Authorization": new_token}
    ).status_code == 200


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
    client, seed_royal, db_session, test_settings, monkeypatch
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
    old_token = client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": _totp(secret, 551),
            "operation_kind": "snapshot",
            "operation_id": "snapshot",
        },
    ).json()["token"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 16_560.0)
    rotated = client.post(
        "/admin/privileged-auth/recovery/rotate",
        json={"password": "secret", "code": _totp(secret, 552)},
    )
    assert rotated.status_code == 200
    assert len(rotated.json()["recovery_codes"]) == 10
    assert {
        row.hash_version
        for row in db_session.scalars(
            select(PrivilegedRecoveryCode).where(PrivilegedRecoveryCode.used_at.is_(None))
        )
    } == {"scrypt-v1"}
    assert db_session.get(PrivilegedCredential, seed_royal.id).credential_generation == 2
    ops_root = Path(test_settings.ops_dir)
    assert client.post(
        "/admin/ops/snapshot", headers={"X-Privileged-Authorization": old_token}
    ).status_code == 401
    assert not ops_root.exists() or not [
        path for path in ops_root.rglob("*") if path.is_file() and path.name != "begin.lock"
    ]


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


def test_reset_lock_serializes_concurrent_old_grant_consumption(
    client, seed_royal, db_session, db_engine, test_settings, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 30_000.0)
    recovery = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 1_000)},
    ).json()["recovery_codes"][0]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 30_030.0)
    raw_token = client.post(
        "/admin/privileged-auth/reauthorize",
        json={
            "password": "secret",
            "code": _totp(secret, 1_001),
            "operation_kind": "snapshot",
            "operation_id": "snapshot",
        },
    ).json()["token"]
    grant = db_session.scalar(
        select(PrivilegedReauthorization).where(
            PrivilegedReauthorization.token_hash
            == hashlib.sha256(raw_token.encode()).hexdigest()
        )
    )
    session_hash = grant.session_token_hash
    begun = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": recovery},
    ).json()
    db_session.commit()

    reset_locked = threading.Event()
    release_reset = threading.Event()
    consume_started = threading.Event()
    consume_done = threading.Event()
    results: dict[str, object] = {}
    original_lock = privileged_auth._lock_credential

    def controlled_lock(db, user_id):
        credential = original_lock(db, user_id)
        if db.info.get("pause_reset"):
            reset_locked.set()
            assert release_reset.wait(5)
        return credential

    monkeypatch.setattr(privileged_auth, "_lock_credential", controlled_lock)
    context = privileged_auth.AuditContext(
        ip="127.0.0.1",
        device="concurrency-test",
        session_token_hash=session_hash,
        operation_kind="snapshot",
        operation_id="snapshot",
    )

    def reset_worker():
        try:
            with Session(db_engine) as session:
                session.info["pause_reset"] = True
                actor = session.get(User, seed_royal.id)
                privileged_auth.confirm_recovery_reset(
                    session,
                    actor,
                    test_settings,
                    pending_id=begun["pending_id"],
                    code=_totp(begun["secret"], 1_001),
                    context=context,
                    throttle=LoginThrottle(
                        max_attempts=5,
                        window_seconds=60,
                        lockout_seconds=60,
                    ),
                    throttle_key="concurrent-reset",
                )
                results["reset"] = "confirmed"
        except BaseException as exc:  # pragma: no cover - asserted below
            results["reset_error"] = exc

    def consume_worker():
        consume_started.set()
        try:
            with Session(db_engine) as session:
                actor = session.get(User, seed_royal.id)
                results["consume"] = privileged_auth.consume_reauthorization(
                    session,
                    actor,
                    raw_token=raw_token,
                    context=context,
                )
        except BaseException as exc:  # pragma: no cover - asserted below
            results["consume_error"] = exc
        finally:
            consume_done.set()

    reset_thread = threading.Thread(target=reset_worker)
    consume_thread = threading.Thread(target=consume_worker)
    reset_thread.start()
    assert reset_locked.wait(5)
    consume_thread.start()
    assert consume_started.wait(1)
    assert not consume_done.wait(0.2)
    release_reset.set()
    reset_thread.join(5)
    consume_thread.join(5)

    assert not reset_thread.is_alive()
    assert not consume_thread.is_alive()
    assert results == {"reset": "confirmed", "consume": False}


def test_reset_audit_failure_rolls_back_pending_and_promotion(
    client, seed_royal, db_session, monkeypatch
):
    login_as(client, "royal", "secret")
    secret = client.post("/admin/privileged-auth/enrollment").json()["secret"]
    monkeypatch.setattr("robopark_api.services.privileged_auth._unix_time", lambda: 33_000.0)
    recovery = client.post(
        "/admin/privileged-auth/enrollment/confirm",
        json={"password": "secret", "code": _totp(secret, 1_100)},
    ).json()["recovery_codes"][0]
    credential = db_session.get(PrivilegedCredential, seed_royal.id)
    old_ciphertext = credential.totp_secret_encrypted

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    with monkeypatch.context() as scoped:
        scoped.setattr(privileged_auth, "_add_audit", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            client.post(
                "/admin/privileged-auth/recovery/reset",
                json={"password": "secret", "code": recovery},
            )
    assert db_session.execute(text("SELECT count(*) FROM privileged_recovery_resets")).scalar_one() == 0
    assert len(
        db_session.scalars(
            select(PrivilegedRecoveryCode).where(PrivilegedRecoveryCode.used_at.is_(None))
        ).all()
    ) == 10

    begun = client.post(
        "/admin/privileged-auth/recovery/reset",
        json={"password": "secret", "code": recovery},
    ).json()
    with monkeypatch.context() as scoped:
        scoped.setattr(privileged_auth, "_add_audit", fail_audit)
        with pytest.raises(RuntimeError, match="audit unavailable"):
            client.post(
                "/admin/privileged-auth/recovery/reset/confirm",
                json={
                    "pending_id": begun["pending_id"],
                    "code": _totp(begun["secret"], 1_100),
                },
            )
    db_session.expire_all()
    credential = db_session.get(PrivilegedCredential, seed_royal.id)
    assert credential.totp_secret_encrypted == old_ciphertext
    assert credential.credential_generation == 1
    assert db_session.execute(text("SELECT count(*) FROM privileged_recovery_resets")).scalar_one() == 1
