import base64
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from conftest import role_id_for
from robopark_api.config import Settings
from robopark_api.emergency_bridge import create_broker_app
from robopark_api.models import AccessStatus, AgentDevice, CorporateWriteAuthority, User
from robopark_api.security import hash_password
from robopark_api.services.compute_broker import ComputeRegistry
from robopark_api.services.emergency_bridge_auth import body_signature


def _headers(settings, body):
    timestamp = str(int(time.time()))
    nonce = str(uuid4())
    return {
        "Content-Type": "application/json",
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(settings.secret_key, timestamp, nonce, body),
    }


def _post(client, settings, path, value):
    body = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return client.post(path, content=body, headers=_headers(settings, body))


def test_corporate_write_pins_one_mac_and_replays_without_redispatch(
    db_engine, db_session, monkeypatch
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="compute-secret",
        emergency_bridge_enabled=True,
        mac_corporate_gateway_enabled=True,
        robopark_environment="test",
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    private_key = ec.generate_private_key(ec.SECP256R1())
    owner = User(
        username="write-owner",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "admin"),
        access_status=AccessStatus.approved.value,
        is_active=True,
        tracker_login="write.owner",
    )
    db_session.add(owner)
    db_session.flush()
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=owner.id,
        label="Write Mac",
        public_key_x963=private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        ),
        environment="test",
        instance_id=str(settings.robopark_instance_id),
        approved_at=datetime.now(UTC),
        approved_by_user_id=owner.id,
    )
    db_session.add(device)
    db_session.commit()
    routing_keys = []
    original_choose = ComputeRegistry.choose

    def choose(registry, *args, **kwargs):
        routing_keys.append(kwargs.get("routing_key"))
        return original_choose(registry, *args, **kwargs)

    monkeypatch.setattr(ComputeRegistry, "choose", choose)
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    command_id = str(uuid4())

    with (
        TestClient(app) as client,
        client.websocket_connect("/emergency/bridge/compute/ws") as websocket,
    ):
        challenge = websocket.receive_json()
        signed = (
            f"robopark-compute-v1\n{settings.robopark_instance_id}\ntest\n{challenge['nonce']}"
        ).encode()
        signature = base64.b64encode(private_key.sign(signed, ec.ECDSA(hashes.SHA256()))).decode()
        websocket.send_json({"type": "auth", "device_id": device.id, "signature": signature})
        ready = websocket.receive_json()
        websocket.send_json(
            {
                "type": "heartbeat",
                "generation": ready["generation"],
                "capacity": 1,
                "active_jobs": 0,
                "cpu_percent": 10,
                "memory_percent": 20,
                "thermal_state": "normal",
                "power": "ac",
                "supports": ["corporate_write_v1", "corporate_tracker_write_v1"],
                "corporate_read_capacity": 0,
                "corporate_write_capacity": 1,
                "corporate_active_jobs": 0,
                "corporate_active_writes": 0,
                "version": "test",
            }
        )
        assert websocket.receive_json() == {
            "type": "heartbeat_ack",
            "generation": ready["generation"],
        }
        prepared = _post(
            client,
            settings,
            "/internal/v1/corporate-write/prepare",
            {
                "command_id": command_id,
                "action": "comment_create",
                "resource": "ROBOPARK-42",
                "requester_user_id": owner.id,
                "allow_server": True,
                "force_server": False,
            },
        )
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["mode"] == "mac"
        assert prepared.json()["device_id"] == device.id
        assert routing_keys[-1] == "tracker:comment_create"

        invalid = _post(
            client,
            settings,
            "/internal/v1/corporate-write/prepare",
            {
                "command_id": str(uuid4()),
                "action": "arbitrary_proxy",
                "resource": "ROBOPARK-42",
                "requester_user_id": owner.id,
            },
        )
        assert invalid.status_code == 422

        request = {
            "command_id": command_id,
            "action": "comment_create",
            "resource": "ROBOPARK-42",
            "params": {"text": "one mutation"},
            "requester_user_id": owner.id,
            "deadline_ms": 2_000,
        }
        holder = {}
        thread = threading.Thread(
            target=lambda: holder.setdefault(
                "response",
                _post(client, settings, "/internal/v1/corporate-write/execute", request),
            )
        )
        thread.start()
        job = websocket.receive_json()
        assert job["operation"] == "corporate_write_v1"
        envelope = json.loads(base64.b64decode(job["payload_base64"]))
        assert envelope["command_id"] == command_id
        assert envelope["owner_tracker_login"] == "write.owner"
        websocket.send_json(
            {
                "type": "compute_result",
                "generation": ready["generation"],
                "job_id": job["job_id"],
                "attempt_id": job["attempt_id"],
                "payload_sha256": hashlib.sha256(
                    base64.b64decode(job["payload_base64"])
                ).hexdigest(),
                "ok": True,
                "result": {
                    "service": "tracker",
                    "action": "comment_create",
                    "resource": "ROBOPARK-42",
                    "owner_user_id": owner.id,
                    "identity_login": "write.owner",
                    "command_id": command_id,
                    "step_id": "comment_create",
                    "status_code": 201,
                    "payload": {"id": "comment-1"},
                },
            }
        )
        acknowledgement = websocket.receive_json()
        assert acknowledgement == {
            "type": "corporate_write_receipt_ack",
            "generation": ready["generation"],
            "command_id": command_id,
            "step_id": "comment_create",
            "owner_user_id": owner.id,
            "device_id": device.id,
        }
        thread.join(3)
        assert holder["response"].status_code == 200, holder["response"].text
        assert holder["response"].json()["payload"] == {"id": "comment-1"}
        replay = _post(client, settings, "/internal/v1/corporate-write/execute", request)
        assert replay.status_code == 200
        assert replay.json()["payload"] == {"id": "comment-1"}

        denied_command = str(uuid4())
        denied_request = {**request, "command_id": denied_command, "params": {"text": "denied"}}
        assert (
            _post(
                client,
                settings,
                "/internal/v1/corporate-write/prepare",
                {
                    "command_id": denied_command,
                    "resource": "ROBOPARK-42",
                    "requester_user_id": owner.id,
                    "allow_server": False,
                    "force_server": False,
                },
            ).status_code
            == 200
        )
        denied_holder = {}
        denied_thread = threading.Thread(
            target=lambda: denied_holder.setdefault(
                "response",
                _post(client, settings, "/internal/v1/corporate-write/execute", denied_request),
            )
        )
        denied_thread.start()
        denied_job = websocket.receive_json()
        websocket.send_json(
            {
                "type": "compute_result",
                "generation": ready["generation"],
                "job_id": denied_job["job_id"],
                "attempt_id": denied_job["attempt_id"],
                "payload_sha256": denied_job["payload_sha256"],
                "ok": False,
                "error": "auth",
            }
        )
        denied_ack = websocket.receive_json()
        assert denied_ack["command_id"] == denied_command
        assert denied_ack["step_id"] == "comment_create"
        denied_thread.join(3)
        assert denied_holder["response"].status_code == 401
        assert denied_holder["response"].json()["detail"] == "corporate_authentication"

    db_session.expire_all()
    assert db_session.get(CorporateWriteAuthority, command_id).device_id == device.id
