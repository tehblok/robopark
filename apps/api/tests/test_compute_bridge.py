import base64
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from conftest import role_id_for
from robopark_api.config import Settings
from robopark_api.emergency_bridge import create_broker_app
from robopark_api.models import AccessStatus, AgentDevice, User
from robopark_api.security import hash_password
from robopark_api.services.emergency_bridge_auth import body_signature


def _signed_headers(settings: Settings, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    nonce = str(uuid4())
    return {
        "Content-Type": "application/json",
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(settings.secret_key, timestamp, nonce, body),
    }


def _device(db_session, settings, *, suffix=""):
    private_key = ec.generate_private_key(ec.SECP256R1())
    owner = User(
        username=f"compute-owner{suffix}",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(owner)
    db_session.flush()
    public_key = private_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=owner.id,
        label="Compute Mac",
        public_key_x963=public_key,
        environment=settings.robopark_environment,
        instance_id=str(settings.robopark_instance_id),
        approved_at=datetime.now(UTC),
        approved_by_user_id=owner.id,
    )
    db_session.add(device)
    db_session.commit()
    return private_key, owner, device


def test_compute_ws_uses_distinct_domain_and_dispatches_hashed_typed_job(db_engine, db_session):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="compute-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    private_key, owner, device = _device(db_session, settings)
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    with (
        TestClient(app) as client,
        client.websocket_connect("/emergency/bridge/compute/ws") as websocket,
    ):
        challenge = websocket.receive_json()
        signed = (
            "robopark-compute-v1\n"
            + str(settings.robopark_instance_id)
            + "\n"
            + settings.robopark_environment
            + "\n"
            + challenge["nonce"]
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
                "supports": ["csv_render_v1"],
                "version": "0.2.3-rc.2",
            }
        )
        raw_payload = json.dumps(
            {"headers": ["Name"], "rows": [["Alice"]]},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        rpc_body = json.dumps(
            {
                "operation": "csv_render_v1",
                "payload_base64": base64.b64encode(raw_payload).decode(),
                "payload_sha256": hashlib.sha256(raw_payload).hexdigest(),
                "requester_user_id": owner.id,
                "deadline_ms": 20_000,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        holder = {}

        def request():
            holder["response"] = client.post(
                "/internal/v1/compute",
                content=rpc_body,
                headers=_signed_headers(settings, rpc_body),
            )

        thread = threading.Thread(target=request)
        thread.start()
        job = websocket.receive_json()
        assert job["type"] == "compute_job"
        assert job["operation"] == "csv_render_v1"
        assert job["payload_sha256"] == hashlib.sha256(raw_payload).hexdigest()
        output = b"\xef\xbb\xbfName\r\nAlice\r\n"
        websocket.send_json(
            {
                "type": "compute_result",
                "generation": ready["generation"],
                "job_id": job["job_id"],
                "attempt_id": job["attempt_id"],
                "payload_sha256": job["payload_sha256"],
                "ok": True,
                "result": {
                    "data_base64": base64.b64encode(output).decode(),
                    "sha256": hashlib.sha256(output).hexdigest(),
                    "mime_type": "text/csv; charset=utf-8",
                },
            }
        )
        thread.join(2)
        response = holder["response"]
        assert response.status_code == 200, response.text
        assert response.json()["device_id"] == device.id
        assert response.json()["result"]["sha256"] == hashlib.sha256(output).hexdigest()


def test_internal_compute_rejects_bad_hash_and_returns_prompt_503_without_devices(
    db_engine, seed_admin
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="compute-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    with TestClient(app) as client:
        body = json.dumps(
            {
                "operation": "image_resize_v1",
                "payload_base64": base64.b64encode(b"{}").decode(),
                "payload_sha256": "0" * 64,
                "requester_user_id": seed_admin.id,
                "deadline_ms": 20_000,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        started = time.monotonic()
        response = client.post(
            "/internal/v1/compute", content=body, headers=_signed_headers(settings, body)
        )
        assert response.status_code == 422
        assert time.monotonic() - started < 1

        image = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        raw_payload = json.dumps(
            {
                "data_base64": base64.b64encode(image).decode(),
                "mime_type": "image/png",
                "max_edge": 1024,
                "quality": 80,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        payload = json.loads(body)
        payload["payload_base64"] = base64.b64encode(raw_payload).decode()
        payload["payload_sha256"] = hashlib.sha256(raw_payload).hexdigest()
        valid = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        started = time.monotonic()
        response = client.post(
            "/internal/v1/compute", content=valid, headers=_signed_headers(settings, valid)
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "no_compute_device"
        assert time.monotonic() - started < 1


def test_direct_signaling_reserves_normal_lease_and_releases_on_peer_done(db_engine, db_session):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="compute-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    private_key, owner, device = _device(db_session, settings)
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    with (
        TestClient(app) as client,
        client.websocket_connect("/emergency/bridge/compute/ws") as websocket,
    ):
        challenge = websocket.receive_json()
        signed = (
            f"robopark-compute-v1\n{settings.robopark_instance_id}\n"
            f"{settings.robopark_environment}\n{challenge['nonce']}"
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
                "supports": ["image_resize_v1", "webrtc_datachannel_v1"],
                "version": "0.2.3-rc.2",
            }
        )
        payload = {
            "operation": "image_resize_v1",
            "payload_sha256": "a" * 64,
            "payload_bytes": 500_000,
            "offer": {"type": "offer", "sdp": "v=0\r\n"},
            "requester_user_id": owner.id,
            "deadline_ms": 20_000,
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        holder = {}

        def request():
            holder["response"] = client.post(
                "/internal/v1/compute-direct",
                content=body,
                headers=_signed_headers(settings, body),
            )

        thread = threading.Thread(target=request)
        thread.start()
        offer = websocket.receive_json()
        assert offer["type"] == "compute_peer_offer"
        assert offer["payload_bytes"] == 500_000
        assert len(base64.urlsafe_b64decode(offer["token"] + "==")) == 32
        assert "payload_base64" not in offer
        websocket.send_json(
            {
                "type": "compute_peer_answer",
                "generation": ready["generation"],
                "job_id": offer["job_id"],
                "attempt_id": offer["attempt_id"],
                "payload_sha256": offer["payload_sha256"],
                "answer": {"type": "answer", "sdp": "v=0\r\na=answer\r\n"},
            }
        )
        thread.join(2)
        response = holder["response"]
        assert response.status_code == 200, response.text
        assert response.json()["token"] == offer["token"]
        assert response.json()["answer"]["type"] == "answer"
        finish_body = json.dumps(
            {
                "job_id": offer["job_id"],
                "requester_user_id": owner.id,
                "outcome": "completed",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        finish = client.post(
            "/internal/v1/compute-direct-finish",
            content=finish_body,
            headers=_signed_headers(settings, finish_body),
        )
        assert finish.status_code == 200, finish.text
        assert app.state.compute_registry.metrics["direct_completed"] == 1
        assert len(app.state.compute_registry.pending) == 1

        repeated = client.post(
            "/internal/v1/compute-direct-finish",
            content=finish_body,
            headers=_signed_headers(settings, finish_body),
        )
        assert repeated.status_code == 200
        assert app.state.compute_registry.metrics["direct_completed"] == 1
        websocket.send_json(
            {
                "type": "compute_peer_done",
                "generation": ready["generation"],
                "job_id": offer["job_id"],
                "attempt_id": offer["attempt_id"],
                "payload_sha256": offer["payload_sha256"],
                "ok": True,
                "input_bytes": 500_000,
                "output_bytes": 100_000,
                "duration_ms": 250,
            }
        )
        deadline = time.monotonic() + 1
        while app.state.compute_registry.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        assert app.state.compute_registry.pending == {}
        assert app.state.compute_registry.metrics["direct_completed"] == 1
        assert app.state.compute_registry.metrics["direct_input_bytes"] == 500_000
        assert app.state.compute_registry.metrics["direct_output_bytes"] == 100_000

        after_done = client.post(
            "/internal/v1/compute-direct-finish",
            content=finish_body,
            headers=_signed_headers(settings, finish_body),
        )
        assert after_done.status_code == 200
        assert app.state.compute_registry.metrics["direct_completed"] == 1

        second_body = json.dumps(
            {**payload, "payload_sha256": "c" * 64}, separators=(",", ":"), sort_keys=True
        ).encode()
        second_holder = {}

        def second_request():
            second_holder["response"] = client.post(
                "/internal/v1/compute-direct",
                content=second_body,
                headers=_signed_headers(settings, second_body),
            )

        second_thread = threading.Thread(target=second_request)
        second_thread.start()
        second_offer = websocket.receive_json()
        websocket.send_json(
            {
                "type": "compute_peer_answer",
                "generation": ready["generation"],
                "job_id": second_offer["job_id"],
                "attempt_id": second_offer["attempt_id"],
                "payload_sha256": second_offer["payload_sha256"],
                "answer": {"type": "answer", "sdp": "v=0\r\na=answer-2\r\n"},
            }
        )
        second_thread.join(2)
        assert second_holder["response"].status_code == 200

        async def fail_cancel(_value):
            raise RuntimeError("socket disappeared")

        app.state.compute_registry.channels[device.id].send_json = fail_cancel
        fallback_body = json.dumps(
            {
                "job_id": second_offer["job_id"],
                "requester_user_id": owner.id,
                "outcome": "fallback",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        fallback = client.post(
            "/internal/v1/compute-direct-finish",
            content=fallback_body,
            headers=_signed_headers(settings, fallback_body),
        )
        assert fallback.status_code == 200
        assert app.state.compute_registry.pending == {}
        assert app.state.compute_registry.metrics["direct_fallback"] == 1

        device.revoked_at = datetime.now(UTC)
        db_session.commit()
        revoked = client.post(
            "/internal/v1/compute-direct-finish",
            content=finish_body,
            headers=_signed_headers(settings, finish_body),
        )
        assert revoked.status_code == 403


@pytest.mark.parametrize("first_failure", ["timeout", "native_failure"])
def test_direct_signaling_retries_once_on_a_different_ready_mac(
    db_engine, db_session, first_failure
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="compute-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    first_key, first_owner, first_device = _device(db_session, settings, suffix="-a")
    second_key, _second_owner, second_device = _device(db_session, settings, suffix="-b")
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)

    with TestClient(app) as client:
        stack = []
        try:
            for key, device in ((first_key, first_device), (second_key, second_device)):
                ws = client.websocket_connect("/emergency/bridge/compute/ws").__enter__()
                stack.append(ws)
                challenge = ws.receive_json()
                signed = (
                    f"robopark-compute-v1\n{settings.robopark_instance_id}\n"
                    f"{settings.robopark_environment}\n{challenge['nonce']}"
                ).encode()
                signature = base64.b64encode(key.sign(signed, ec.ECDSA(hashes.SHA256()))).decode()
                ws.send_json({"type": "auth", "device_id": device.id, "signature": signature})
                ready = ws.receive_json()
                ws.send_json(
                    {
                        "type": "heartbeat",
                        "generation": ready["generation"],
                        "capacity": 1,
                        "active_jobs": 0,
                        "cpu_percent": 10,
                        "memory_percent": 20,
                        "thermal_state": "normal",
                        "power": "ac",
                        "supports": ["image_resize_v1", "webrtc_datachannel_v1"],
                    }
                )

            indexed = {first_device.id: stack[0], second_device.id: stack[1]}
            selected_ids = sorted(indexed)
            body = json.dumps(
                {
                    "operation": "image_resize_v1",
                    "payload_sha256": "b" * 64,
                    "payload_bytes": 500_000,
                    "offer": {"type": "offer", "sdp": "v=0\r\n"},
                    "requester_user_id": first_owner.id,
                    "deadline_ms": 400,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            holder = {}

            def request():
                holder["response"] = client.post(
                    "/internal/v1/compute-direct",
                    content=body,
                    headers=_signed_headers(settings, body),
                )

            thread = threading.Thread(target=request)
            thread.start()
            first_offer = indexed[selected_ids[0]].receive_json()
            assert first_offer["type"] == "compute_peer_offer"
            if first_failure == "native_failure":
                indexed[selected_ids[0]].send_json(
                    {
                        "type": "compute_peer_done",
                        "generation": first_offer["generation"],
                        "job_id": first_offer["job_id"],
                        "attempt_id": first_offer["attempt_id"],
                        "payload_sha256": first_offer["payload_sha256"],
                        "ok": False,
                        "input_bytes": 0,
                        "output_bytes": 0,
                        "duration_ms": 1,
                    }
                )
            second_offer = indexed[selected_ids[1]].receive_json()
            assert second_offer["type"] == "compute_peer_offer"
            assert second_offer["job_id"] != first_offer["job_id"]
            indexed[selected_ids[1]].send_json(
                {
                    "type": "compute_peer_answer",
                    "generation": second_offer["generation"],
                    "job_id": second_offer["job_id"],
                    "attempt_id": second_offer["attempt_id"],
                    "payload_sha256": second_offer["payload_sha256"],
                    "answer": {"type": "answer", "sdp": "v=0\r\na=answer\r\n"},
                }
            )
            thread.join(2)
            assert holder["response"].status_code == 200, holder["response"].text
            assert holder["response"].json()["device_id"] == selected_ids[1]
            assert app.state.compute_registry.metrics["direct_retries"] == 1
            assert app.state.compute_registry.metrics["direct_failed"] == (
                1 if first_failure == "native_failure" else 0
            )
        finally:
            for ws in reversed(stack):
                ws.__exit__(None, None, None)


def test_compute_status_is_owner_scoped_except_for_privileged_users(
    db_engine, db_session, seed_admin
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="compute-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    _, owner, owned = _device(db_session, settings)
    admin_device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=seed_admin.id,
        label="Admin Mac",
        public_key_x963=(
            ec.generate_private_key(ec.SECP256R1())
            .public_key()
            .public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        ),
        environment=settings.robopark_environment,
        instance_id=str(settings.robopark_instance_id),
        approved_at=datetime.now(UTC),
        approved_by_user_id=seed_admin.id,
    )
    db_session.add(admin_device)
    db_session.commit()
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)

    def status(client, requester_user_id):
        body = json.dumps(
            {"requester_user_id": requester_user_id}, separators=(",", ":"), sort_keys=True
        ).encode()
        return client.post(
            "/internal/v1/compute-status",
            content=body,
            headers=_signed_headers(settings, body),
        )

    with TestClient(app) as client:
        own = status(client, owner.id)
        privileged = status(client, seed_admin.id)

    assert own.status_code == 200
    assert own.json()["scope"] == "own"
    assert [item["id"] for item in own.json()["devices"]] == [owned.id]
    assert privileged.status_code == 200
    assert privileged.json()["scope"] == "all"
    assert {item["id"] for item in privileged.json()["devices"]} == {
        owned.id,
        admin_device.id,
    }


def test_wire_frame_accepts_maximum_decoded_csv_and_rejects_oversized_envelope():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest

    from robopark_api.compute_bridge import MAX_FRAME_BYTES, _receive_json
    from robopark_api.services.compute_broker import MAX_RESULT_BYTES

    encoded = base64.b64encode(b"x" * MAX_RESULT_BYTES).decode()
    frame = json.dumps({"type": "compute_result", "result": {"data_base64": encoded}})
    socket = SimpleNamespace(receive_text=AsyncMock(return_value=frame))
    assert asyncio.run(_receive_json(socket))["result"]["data_base64"] == encoded
    socket.receive_text.return_value = " " * (MAX_FRAME_BYTES + 1)
    with pytest.raises(ValueError, match="frame_too_large"):
        asyncio.run(_receive_json(socket))
