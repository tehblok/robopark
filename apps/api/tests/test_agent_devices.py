import base64
import json

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import func, select

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, AgentDevice, AgentEnrollment, AuditLog, User
from robopark_api.routers import agent_devices
from robopark_api.routers.agent_devices import ClaimRateLimiter
from robopark_api.security import hash_password
from robopark_api.services import emergency_bridge_client, rbac


def _public_key() -> str:
    raw = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    return base64.b64encode(raw).decode("ascii")


def test_info_is_public_and_does_not_expose_secrets(client):
    response = client.get("/agent-devices/info")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "environment": "production",
        "instance_id": "00000000-0000-0000-0000-000000000000",
        "protocol": 1,
        "claim_url": "/api/agent-devices/claim",
        "websocket_url": "/api/emergency/bridge/ws",
        "compute_websocket_url": "/api/emergency/bridge/compute/ws",
        "signed_profile": None,
    }


def test_info_exposes_only_bounded_signed_deployment_profile(client, tmp_path, monkeypatch):
    profile = tmp_path / "deployment-profile.json"
    payload = base64.b64encode(json.dumps({"origin": "https://test.example"}).encode()).decode()
    envelope = {"payload": payload, "signature": "c2ln"}
    profile.write_text(json.dumps(envelope))
    monkeypatch.setattr(agent_devices, "_DEPLOYMENT_PROFILE", profile)

    response = client.get("/agent-devices/info")

    assert response.status_code == 200
    assert response.json()["signed_profile"] == envelope

    profile.write_text("{}")
    assert client.get("/agent-devices/info").json()["signed_profile"] is None

    profile.write_bytes(b"x" * (16 * 1024 + 1))
    assert client.get("/agent-devices/info").json()["signed_profile"] is None

    target = tmp_path / "target.json"
    target.write_text(json.dumps(envelope))
    profile.unlink()
    profile.symlink_to(target)
    assert client.get("/agent-devices/info").json()["signed_profile"] is None


def test_enrollment_claim_is_one_use_auto_approved_and_private_key_never_enters_api(
    client, db_session, seed_admin
):
    assert login_as(client, "admin", "secret").status_code == 204
    issued = client.post("/agent-devices/enrollments", json={"label": "Test Mac"})
    assert issued.status_code == 201
    assert issued.headers["cache-control"] == "no-store"
    code = issued.json()["code"]

    claim = {
        "code": code,
        "label": "Test Mac",
        "public_key": _public_key(),
        "environment": "production",
        "instance_id": "00000000-0000-0000-0000-000000000000",
    }
    first = client.post("/agent-devices/claim", json=claim)
    second = client.post("/agent-devices/claim", json=claim)

    assert first.status_code == 201
    assert first.json()["protocol"] == 1
    assert second.status_code == 409
    device = db_session.get(AgentDevice, first.json()["device_id"])
    assert device is not None
    assert first.json()["status"] == "approved"
    assert device.approved_at is not None
    assert device.approved_by_user_id == seed_admin.id
    assert device.public_key_x963 != claim["public_key"]
    assert not hasattr(device, "private_key")
    audit_entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "agent_device.enrolled", AuditLog.target_id == device.id
        )
    )
    assert audit_entry is not None
    assert audit_entry.actor_user_id == seed_admin.id
    assert code not in (audit_entry.detail or "")


def test_authenticated_first_login_claims_device_without_manual_code_and_is_idempotent(
    client, db_session, seed_admin
):
    login_as(client, "admin", "secret")
    payload = {
        "label": "Desk Mac",
        "public_key": _public_key(),
        "environment": "production",
        "instance_id": "00000000-0000-0000-0000-000000000000",
    }

    first = client.post("/agent-devices/claim-auth", json=payload)
    repeated = client.post("/agent-devices/claim-auth", json=payload)

    assert first.status_code == 201
    assert repeated.status_code == 200
    assert repeated.json()["device_id"] == first.json()["device_id"]
    assert repeated.json()["owner_user_id"] == seed_admin.id
    device = db_session.get(AgentDevice, first.json()["device_id"])
    assert device is not None
    assert device.owner_user_id == seed_admin.id
    assert device.approved_by_user_id == seed_admin.id


def test_authenticated_compute_registration_does_not_require_emergency_permission(
    client, seed_admin, monkeypatch
):
    login_as(client, "admin", "secret")
    monkeypatch.setattr(rbac, "has_permission", lambda *_args, **_kwargs: False)

    response = client.post(
        "/agent-devices/claim-auth",
        json={
            "label": "Compute-only Mac",
            "public_key": _public_key(),
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["owner_user_id"] == seed_admin.id


def test_authenticated_claim_cannot_move_key_to_another_account_or_restore_revocation(
    client, db_session, seed_admin
):
    public_key = _public_key()
    payload = {
        "label": "Bound Mac",
        "public_key": public_key,
        "environment": "production",
        "instance_id": "00000000-0000-0000-0000-000000000000",
    }
    login_as(client, "admin", "secret")
    claimed = client.post("/agent-devices/claim-auth", json=payload)
    assert claimed.status_code == 201

    operator = User(
        username="device-account-switch",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.commit()
    login_as(client, operator.username, "secret")

    switched = client.post("/agent-devices/claim-auth", json=payload)
    assert switched.status_code == 409
    assert switched.json()["detail"] == "device_owner_mismatch"
    assert db_session.get(AgentDevice, claimed.json()["device_id"]).owner_user_id == seed_admin.id

    login_as(client, "admin", "secret")
    assert client.delete(f"/agent-devices/{claimed.json()['device_id']}").status_code == 204
    revoked = client.post("/agent-devices/claim-auth", json=payload)
    assert revoked.status_code == 409
    assert revoked.json()["detail"] == "device_revoked"


def test_claim_rejects_environment_or_instance_without_consuming_code(client, seed_admin):
    login_as(client, "admin", "secret")
    code = client.post("/agent-devices/enrollments", json={"label": "Mac"}).json()["code"]
    bad = client.post(
        "/agent-devices/claim",
        json={
            "code": code,
            "label": "Mac",
            "public_key": _public_key(),
            "environment": "test",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    good = client.post(
        "/agent-devices/claim",
        json={
            "code": code,
            "label": "Mac",
            "public_key": _public_key(),
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )

    assert bad.status_code == 400
    assert good.status_code == 201


def test_owner_can_list_and_revoke_device(client, db_session, seed_admin):
    login_as(client, "admin", "secret")
    code = client.post("/agent-devices/enrollments", json={"label": "Mac"}).json()["code"]
    claimed = client.post(
        "/agent-devices/claim",
        json={
            "code": code,
            "label": "Mac",
            "public_key": _public_key(),
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    ).json()

    listed = client.get("/agent-devices")
    revoked = client.delete(f"/agent-devices/{claimed['device_id']}")

    assert [item["id"] for item in listed.json()["devices"]] == [claimed["device_id"]]
    assert revoked.status_code == 204
    assert db_session.get(AgentDevice, claimed["device_id"]).revoked_at is not None


def test_claimed_device_trusts_approved_service_account_and_remains_revocable(
    client, db_session, seed_admin
):
    operator = User(
        username="operator-device",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.commit()
    login_as(client, "operator-device", "secret")
    code = client.post("/agent-devices/enrollments", json={"label": "Operator Mac"}).json()["code"]
    device_id = client.post(
        "/agent-devices/claim",
        json={
            "code": code,
            "label": "Operator Mac",
            "public_key": _public_key(),
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    ).json()["device_id"]

    own = client.get("/agent-devices").json()["devices"][0]
    assert own["status"] == "approved"
    assert own["approved_by_user_id"] == operator.id

    login_as(client, "admin", "secret")
    devices = client.get("/agent-devices?all=true").json()["devices"]
    assert any(
        item["id"] == device_id
        and item["owner_username"] == "operator-device"
        and item["status"] == "approved"
        for item in devices
    )
    device = db_session.get(AgentDevice, device_id)
    assert device.approved_at is not None
    assert device.approved_by_user_id == operator.id

    assert client.delete(f"/agent-devices/{device_id}").status_code == 204
    assert db_session.get(AgentDevice, device_id).revoked_at is not None


def test_claim_enforces_per_user_device_limit(client, seed_admin, test_settings):
    test_settings.agent_device_max_per_user = 1
    login_as(client, "admin", "secret")

    def issue_and_claim():
        code = client.post("/agent-devices/enrollments", json={"label": "Mac"}).json()["code"]
        return client.post(
            "/agent-devices/claim",
            json={
                "code": code,
                "label": "Mac",
                "public_key": _public_key(),
                "environment": "production",
                "instance_id": "00000000-0000-0000-0000-000000000000",
            },
        )

    assert issue_and_claim().status_code == 201
    assert issue_and_claim().status_code == 409


def test_new_code_recovers_active_device_with_same_key_at_owner_limit(
    client, db_session, seed_admin, test_settings
):
    test_settings.agent_device_max_per_user = 1
    login_as(client, "admin", "secret")
    public_key = _public_key()

    def issue_and_claim(key: str):
        code = client.post("/agent-devices/enrollments", json={"label": "Desk Mac"}).json()["code"]
        return client.post(
            "/agent-devices/claim",
            json={
                "code": code,
                "label": "Desk Mac",
                "public_key": key,
                "environment": "production",
                "instance_id": "00000000-0000-0000-0000-000000000000",
            },
        )

    original = issue_and_claim(public_key)
    recovered = issue_and_claim(public_key)

    assert original.status_code == 201
    assert recovered.status_code == 201
    assert recovered.json()["device_id"] == original.json()["device_id"]
    assert db_session.scalar(select(func.count()).select_from(AgentDevice)) == 1
    enrollments = list(db_session.scalars(select(AgentEnrollment)).all())
    assert len(enrollments) == 2
    assert all(enrollment.consumed_at is not None for enrollment in enrollments)
    assert issue_and_claim(_public_key()).status_code == 409


def test_recovery_rejects_revoked_key_and_cross_owner_identity(
    client, db_session, seed_admin, test_settings
):
    test_settings.agent_device_max_per_user = 1
    login_as(client, "admin", "secret")
    public_key = _public_key()

    def issue_and_claim(key: str):
        code = client.post("/agent-devices/enrollments", json={"label": "Desk Mac"}).json()["code"]
        return client.post(
            "/agent-devices/claim",
            json={
                "code": code,
                "label": "Desk Mac",
                "public_key": key,
                "environment": "production",
                "instance_id": "00000000-0000-0000-0000-000000000000",
            },
        )

    original = issue_and_claim(public_key)
    assert original.status_code == 201
    assert client.delete(f"/agent-devices/{original.json()['device_id']}").status_code == 204

    after_revoke = issue_and_claim(public_key)
    assert after_revoke.status_code == 409
    assert after_revoke.json()["detail"] == "invalid_code"

    operator = User(
        username="other-device-owner",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.commit()
    assert login_as(client, "other-device-owner", "secret").status_code == 204

    other_owner = issue_and_claim(public_key)
    assert other_owner.status_code == 409
    assert other_owner.json()["detail"] == "invalid_code"
    assert db_session.scalar(select(func.count()).select_from(AgentDevice)) == 1


def test_public_claim_rate_limiter_is_bounded_per_client_and_memory():
    limiter = ClaimRateLimiter(limit=2, window_seconds=60, max_clients=2)

    assert limiter.allow("client", now=100) is True
    assert limiter.allow("client", now=101) is True
    assert limiter.allow("client", now=102) is False
    assert limiter.allow("other", now=102) is True
    assert limiter.allow("third", now=102) is True
    assert len(limiter._attempts) == 2
    assert limiter.allow("client", now=161) is True


def test_setup_page_is_public_and_uses_csp_compatible_external_script(client):
    page = client.get("/agent-devices/setup")
    script = client.get("/agent-devices/setup.js")

    assert page.status_code == 200
    assert '<script src="/api/agent-devices/setup.js" defer></script>' in page.text
    assert "create.onclick" not in page.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("application/javascript")
    assert script.headers["cache-control"] == "no-store"
    assert "?all=true" in script.text
    assert "Одобрить" not in script.text
    assert "доверяю этому Mac" not in script.text


def test_claim_rejects_non_ascii_code_as_controlled_client_error(client):
    response = client.post(
        "/agent-devices/claim",
        json={
            "code": "ТЕСТ-ТЕСТ-ТЕСТ",
            "label": "Mac",
            "public_key": _public_key(),
            "environment": "production",
            "instance_id": "00000000-0000-0000-0000-000000000000",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_code"


def test_pool_status_returns_private_broker_projection_for_current_user(
    client, seed_admin, test_settings, monkeypatch
):
    test_settings.emergency_bridge_enabled = True
    projected = {
        "enabled": True,
        "scope": "all",
        "summary": {"connected": 1, "ready": 1, "busy": 0, "paused": 0},
        "devices": [{"id": "mac-1", "label": "Desk Mac", "status": "ready"}],
    }
    captured = {}

    def fake_status(*, requester_user_id, settings):
        captured["requester_user_id"] = requester_user_id
        captured["settings"] = settings
        return projected

    monkeypatch.setattr(emergency_bridge_client, "fetch_pool_status", fake_status)
    login_as(client, "admin", "secret")

    response = client.get("/agent-devices/pool-status")

    assert response.status_code == 200
    assert response.json() == projected
    assert captured == {"requester_user_id": seed_admin.id, "settings": test_settings}


def test_pool_status_disabled_is_local_and_does_not_call_broker(
    client, seed_admin, test_settings, monkeypatch
):
    test_settings.emergency_bridge_enabled = False
    monkeypatch.setattr(
        emergency_bridge_client,
        "fetch_pool_status",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("broker must not be called")),
    )
    login_as(client, "admin", "secret")

    response = client.get("/agent-devices/pool-status")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["devices"] == []


def test_automatic_probe_target_uses_authorized_registry_without_emergency_calls(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import emergency_client, platform_settings, tracker_client

    assert client.get("/agent-devices/probe-target").status_code == 401
    login_as(client, "mech1", "secret")
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "fixture-token")
    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **kwargs: [
            {"key": "ROBOPARK-FOREIGN", "queue": "ROBOPARK", "tags": ["Foreign"], "robot": "1"},
            {
                "key": "ROBOPARK-OWN",
                "queue": "ROBOPARK",
                "tags": [seed_park_with_tracker.tag],
                "robot": "447",
            },
        ],
    )

    def unexpected_read(**kwargs):
        raise AssertionError("Probe discovery must not read Emergency on the server")

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", unexpected_read)
    response = client.get("/agent-devices/probe-target")
    assert response.status_code == 200
    assert response.json() == {"vin": "YASADR00000000447"}
    assert response.headers["cache-control"] == "no-store"


def test_automatic_probe_target_has_no_guessed_robot_when_registry_empty(
    client, seed_admin, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import platform_settings, tracker_client

    login_as(client, "admin", "secret")
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "fixture-token")
    monkeypatch.setattr(tracker_client, "search_issues", lambda **kwargs: [])
    response = client.get("/agent-devices/probe-target")
    assert response.status_code == 200
    assert response.json() == {"vin": None}
