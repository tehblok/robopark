import base64
import json

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, AgentDevice, AuditLog, User
from robopark_api.routers import agent_devices
from robopark_api.routers.agent_devices import ClaimRateLimiter
from robopark_api.security import hash_password


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


def test_enrollment_claim_is_one_use_and_private_key_never_enters_api(
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
    assert device.approved_at is None
    assert device.public_key_x963 != claim["public_key"]
    assert not hasattr(device, "private_key")


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


def test_claimed_device_requires_admin_approval_and_records_actor(client, db_session, seed_admin):
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

    assert client.get("/agent-devices").json()["devices"][0]["status"] == "pending"
    assert client.post(f"/agent-devices/{device_id}/approve").status_code == 403

    login_as(client, "admin", "secret")
    pending = client.get("/agent-devices?all=true").json()["devices"]
    assert any(
        item["id"] == device_id
        and item["owner_username"] == "operator-device"
        and item["status"] == "pending"
        for item in pending
    )
    approved = client.post(f"/agent-devices/{device_id}/approve")

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    device = db_session.get(AgentDevice, device_id)
    assert device.approved_at is not None
    assert device.approved_by_user_id == seed_admin.id
    entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "admin.agent_device.approved",
            AuditLog.target_id == device_id,
        )
    )
    assert entry is not None
    assert entry.actor_user_id == seed_admin.id


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
    assert "доверяю этому Mac как источнику для других пользователей" in script.text


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
