import asyncio
import base64
import concurrent.futures
import json
import math
import threading
import time
from datetime import UTC, datetime
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from robopark_api.config import Settings
from robopark_api.emergency_bridge import (
    _job_authority_valid,
    _validated_robot_payload_size,
    create_broker_app,
)
from robopark_api.models import AgentDevice
from robopark_api.services import emergency_scope
from robopark_api.services.emergency_bridge_auth import body_signature
from robopark_api.services.emergency_bridge_broker import BrokerRegistry, Channel


class Socket:
    def __init__(self):
        self.sent = []

    async def send_json(self, value):
        self.sent.append(value)


class ReplySocket(Socket):
    def __init__(self, registry, device_id):
        super().__init__()
        self.registry = registry
        self.device_id = device_id

    async def send_json(self, value):
        await super().send_json(value)
        self.registry.resolve_result(
            device_id=self.device_id,
            generation=value["generation"],
            job_id=value["job_id"],
            attempt_id=value["attempt_id"],
            result={
                "ok": True,
                "payload": {
                    "vin": value["vin"],
                    "timestamp": int(time.time() * 1000),
                    "isOnline": True,
                },
            },
        )


class GateReplySocket(ReplySocket):
    def __init__(self, registry, device_id):
        super().__init__(registry, device_id)
        self.release = threading.Event()

    async def send_json(self, value):
        self.sent.append(value)
        await asyncio.to_thread(self.release.wait, 2)
        self.registry.resolve_result(
            device_id=self.device_id,
            generation=value["generation"],
            job_id=value["job_id"],
            attempt_id=value["attempt_id"],
            result={
                "ok": True,
                "payload": {
                    "vin": value["vin"],
                    "timestamp": int(time.time() * 1000),
                    "isOnline": True,
                },
            },
        )


def _channel(
    device: str,
    latency: float,
    *,
    inflight: int = 0,
    ready: bool = True,
    generation: str | None = None,
):
    return Channel(
        device_id=device,
        owner_user_id=1,
        websocket=Socket(),
        generation=generation or f"gen-{device}",
        latency_ms=latency,
        inflight=inflight,
        ready=ready,
    )


def test_selector_uses_at_least_two_devices_and_spreads_equal_work():
    registry = BrokerRegistry(max_connections=100)
    first = _channel("one", 10)
    second = _channel("two", 11)
    registry.replace(first)
    registry.replace(second)

    selected = []
    for _ in range(100):
        channel = registry.choose({"one", "two"})
        selected.append(channel.device_id)
        channel.inflight = 1 - channel.inflight

    assert set(selected) == {"one", "two"}


def test_selector_reports_zero_or_all_busy():
    registry = BrokerRegistry(max_connections=100)
    assert registry.choose(set()) is None
    registry.replace(_channel("busy", 1, inflight=2))
    assert registry.choose({"busy"}) is None


def test_robot_payload_validation_accepts_real_shape_and_rejects_untrusted_structures():
    vin = "YASADR00000000447"
    valid = {
        "vin": vin,
        "timestamp": 1_700_000_000_000,
        "isOnline": True,
        "errors": [],
        "position": {"lat": 1.0, "lon": 2.0},
    }
    assert _validated_robot_payload_size(valid, vin, now_ms=2_000_000_000_000)

    mismatched = {**valid, "vin": "YASADR00000000448"}
    non_finite = {**valid, "position": {"lat": math.nan}}
    oversized = {**valid, "debug": "x" * (2 * 1024 * 1024)}
    deep: dict = {"value": 1}
    for _ in range(20):
        deep = {"nested": deep}
    deep_payload = {**valid, "debug": deep}

    for payload in (mismatched, non_finite, oversized, deep_payload):
        assert _validated_robot_payload_size(payload, vin, now_ms=2_000_000_000_000) is None


def test_registry_bounds_active_connections_at_one_hundred():
    registry = BrokerRegistry(max_connections=100)
    for index in range(100):
        registry.replace(_channel(f"mac-{index}", index + 1))

    assert len(registry.channels) == 100
    try:
        registry.replace(_channel("overflow", 1))
    except OverflowError as exc:
        assert str(exc) == "connection_limit"
    else:
        raise AssertionError("101st connection must be rejected")


def test_reconnect_replaces_generation_and_late_result_is_rejected():
    async def scenario():
        registry = BrokerRegistry(max_connections=100)
        old = _channel("mac", 1, generation="old")
        registry.replace(old)
        job = registry.new_pending(old, "YASADR00000000447")
        registry.replace(_channel("mac", 1, generation="new"))

        accepted = registry.resolve_result(
            device_id="mac",
            generation=old.generation,
            job_id=job.job_id,
            attempt_id=job.attempt_id,
            result={"ok": True, "payload": {"vin": "447"}},
        )

        assert accepted is False
        assert isinstance(job.future.exception(), ConnectionError)

    asyncio.run(scenario())


def test_success_cache_is_scoped_by_device_generation_and_vin():
    async def scenario():
        registry = BrokerRegistry(max_connections=100, cache_ttl_seconds=3)
        channel = _channel("mac", 1)
        registry.cache_put(channel, "VIN1", {"value": 1})
        assert registry.cache_get(channel, "VIN1") == {"value": 1}
        assert registry.cache_get(channel, "VIN2") is None
        replacement = _channel("mac", 1, generation="replacement")
        assert registry.cache_get(replacement, "VIN1") is None

    asyncio.run(scenario())


def test_same_device_generation_and_vin_share_one_inflight_load():
    async def scenario():
        registry = BrokerRegistry(max_connections=100)
        channel = _channel("mac", 1)
        leader, is_leader = registry.begin_load(channel, "VIN1")
        follower, is_follower_leader = registry.begin_load(channel, "VIN1")
        assert is_leader is True
        assert is_follower_leader is False
        assert follower is leader
        registry.finish_load(channel, "VIN1", payload={"value": 1})
        assert await follower == {"value": 1}

    asyncio.run(scenario())


def test_global_singleflight_is_keyed_by_requester_and_vin():
    async def scenario():
        registry = BrokerRegistry(max_connections=100)
        leader, is_leader = registry.begin_request(7, "VIN1")
        follower, is_follower_leader = registry.begin_request(7, "VIN1")
        other, is_other_leader = registry.begin_request(8, "VIN1")
        assert is_leader is True
        assert is_follower_leader is False
        assert follower is leader
        assert is_other_leader is True
        registry.finish_request(7, "VIN1", result=({"value": 1}, "mac"))
        assert await follower == ({"value": 1}, "mac")
        other.cancel()

    asyncio.run(scenario())


def test_cache_evicts_by_serialized_byte_budget():
    registry = BrokerRegistry(max_connections=100, max_cache_bytes=45)
    channel = _channel("mac", 1)
    registry.cache_put(channel, "VIN1", {"value": "a" * 20})
    registry.cache_put(channel, "VIN2", {"value": "b" * 20})

    assert registry.cache_get(channel, "VIN1") is None
    assert registry.cache_get(channel, "VIN2") == {"value": "b" * 20}


def test_pending_jobs_have_a_global_bound():
    async def scenario():
        registry = BrokerRegistry(max_connections=100, max_pending_jobs=1)
        first = _channel("one", 1)
        second = _channel("two", 1)
        job = registry.new_pending(first, "VIN1", requester_user_id=1)
        try:
            registry.new_pending(second, "VIN2", requester_user_id=1)
        except OverflowError as exc:
            assert str(exc) == "pending_limit"
        else:
            raise AssertionError("pending limit must reject excess work")
        job.future.cancel()

    asyncio.run(scenario())


def test_pending_overflow_clears_device_singleflight(db_engine, db_session, seed_admin):
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=seed_admin.id,
        label="Mac",
        public_key_x963=b"\x04" + b"x" * 64,
        environment="production",
        instance_id="11111111-1111-1111-1111-111111111111",
        approved_at=datetime.now(UTC),
        approved_by_user_id=seed_admin.id,
    )
    db_session.add(device)
    db_session.commit()
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    registry = app.state.registry
    registry.max_pending_jobs = 0
    channel = _channel(device.id, 1)
    registry.replace(channel)
    body = json.dumps(
        {
            "vin": "YASADR00000000447",
            "requester_user_id": seed_admin.id,
            "attempt_budget": 1,
            "deadline_ms": 1000,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    timestamp = str(int(time.time()))
    nonce = "pending-overflow"
    headers = {
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(settings.secret_key, timestamp, nonce, body),
        "Content-Type": "application/json",
    }

    with TestClient(app) as client:
        response = client.post("/internal/v1/robot-payload", content=body, headers=headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "all_agents_busy", "attempts_used": 0}
    assert registry.loads == {}


def test_silent_first_agent_reserves_time_for_second_attempt(db_engine, db_session, seed_admin):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    devices = []
    for label in ("Silent", "Responsive"):
        device = AgentDevice(
            id=str(uuid4()),
            owner_user_id=seed_admin.id,
            label=label,
            public_key_x963=b"\x04" + b"x" * 64,
            environment="production",
            instance_id=str(settings.robopark_instance_id),
            approved_at=datetime.now(UTC),
            approved_by_user_id=seed_admin.id,
        )
        db_session.add(device)
        devices.append(device)
    db_session.commit()
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    registry = app.state.registry
    silent = _channel(devices[0].id, 1)
    responsive = _channel(devices[1].id, 2)
    responsive.websocket = ReplySocket(registry, responsive.device_id)
    registry.replace(silent)
    registry.replace(responsive)
    body = json.dumps(
        {
            "vin": "YASADR00000000447",
            "requester_user_id": seed_admin.id,
            "attempt_budget": 2,
            "deadline_ms": 800,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    timestamp = str(int(time.time()))
    nonce = "two-attempt-deadline"
    headers = {
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(settings.secret_key, timestamp, nonce, body),
        "Content-Type": "application/json",
    }

    started = time.monotonic()
    with TestClient(app) as client:
        response = client.post("/internal/v1/robot-payload", content=body, headers=headers)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.json()["device_id"] == responsive.device_id
    assert response.json()["attempts_used"] == 2
    assert silent.websocket.sent[0]["timeout_ms"] <= 400
    assert elapsed < 0.8


def test_device_singleflight_denial_does_not_send_replacement_job(
    db_engine, db_session, seed_admin, seed_royal
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=seed_admin.id,
        label="Shared Mac",
        public_key_x963=b"\x04" + b"x" * 64,
        environment="production",
        instance_id=str(settings.robopark_instance_id),
        approved_at=datetime.now(UTC),
        approved_by_user_id=seed_admin.id,
    )
    db_session.add(device)
    db_session.commit()
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    registry = app.state.registry
    channel = _channel(device.id, 1)
    socket = GateReplySocket(registry, device.id)
    channel.websocket = socket
    registry.replace(channel)
    follower_waiting = threading.Event()
    original_begin_load = registry.begin_load

    def tracked_begin_load(*args, **kwargs):
        result = original_begin_load(*args, **kwargs)
        if result[1] is False:
            follower_waiting.set()
        return result

    registry.begin_load = tracked_begin_load

    def signed_request(user_id: int, nonce: str) -> tuple[bytes, dict[str, str]]:
        body = json.dumps(
            {
                "vin": "YASADR00000000447",
                "requester_user_id": user_id,
                "attempt_budget": 1,
                "deadline_ms": 1500,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        timestamp = str(int(time.time()))
        return body, {
            "X-Robopark-Timestamp": timestamp,
            "X-Robopark-Nonce": nonce,
            "X-Robopark-Signature": body_signature(settings.secret_key, timestamp, nonce, body),
            "Content-Type": "application/json",
        }

    first_body, first_headers = signed_request(seed_admin.id, "singleflight-leader")
    second_body, second_headers = signed_request(seed_royal.id, "singleflight-follower")
    with TestClient(app) as client, concurrent.futures.ThreadPoolExecutor() as pool:
        first = pool.submit(
            client.post,
            "/internal/v1/robot-payload",
            content=first_body,
            headers=first_headers,
        )
        while not socket.sent:
            time.sleep(0.01)
        second = pool.submit(
            client.post,
            "/internal/v1/robot-payload",
            content=second_body,
            headers=second_headers,
        )
        assert follower_waiting.wait(1)
        seed_royal.access_status = "rejected"
        db_session.commit()
        socket.release.set()
        first_response = first.result(timeout=2)
        second_response = second.result(timeout=2)

    assert first_response.status_code == 200
    assert second_response.status_code == 503
    assert second_response.json()["detail"] == "upstream_failed"
    assert len(socket.sent) == 1


def test_websocket_authenticates_registered_p256_device(db_engine, db_session, seed_admin):
    private_key = ec.generate_private_key(ec.SECP256R1())
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=seed_admin.id,
        label="Mac",
        public_key_x963=private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        ),
        environment="test",
        instance_id="11111111-1111-1111-1111-111111111111",
        approved_at=datetime.now(UTC),
        approved_by_user_id=seed_admin.id,
    )
    db_session.add(device)
    db_session.commit()
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_environment="test",
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)

    with TestClient(app).websocket_connect("/emergency/bridge/ws") as ws:
        challenge = ws.receive_json()
        signed = (
            "robopark-bridge-v1\n"
            + challenge["instance_id"]
            + "\n"
            + challenge["environment"]
            + "\n"
            + challenge["nonce"]
        ).encode()
        signature = private_key.sign(signed, ec.ECDSA(hashes.SHA256()))
        ws.send_json(
            {
                "type": "auth",
                "device_id": device.id,
                "signature": base64.b64encode(signature).decode(),
            }
        )
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["max_inflight"] == 2


def test_pending_device_cannot_open_bridge(db_engine, db_session, seed_admin):
    private_key = ec.generate_private_key(ec.SECP256R1())
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=seed_admin.id,
        label="Pending Mac",
        public_key_x963=private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        ),
        environment="test",
        instance_id="11111111-1111-1111-1111-111111111111",
    )
    db_session.add(device)
    db_session.commit()
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_environment="test",
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)

    with TestClient(app).websocket_connect("/emergency/bridge/ws") as ws:
        challenge = ws.receive_json()
        signed = (
            "robopark-bridge-v1\n"
            + challenge["instance_id"]
            + "\n"
            + challenge["environment"]
            + "\n"
            + challenge["nonce"]
        ).encode()
        ws.send_json(
            {
                "type": "auth",
                "device_id": device.id,
                "signature": base64.b64encode(
                    private_key.sign(signed, ec.ECDSA(hashes.SHA256()))
                ).decode(),
            }
        )
        try:
            ws.receive_json()
        except WebSocketDisconnect:
            pass
        else:
            raise AssertionError("pending device must not become ready")


def test_internal_rpc_requires_fresh_one_use_hmac(db_engine, seed_admin):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    body = json.dumps(
        {
            "vin": "YASADR00000000447",
            "requester_user_id": seed_admin.id,
            "attempt_budget": 1,
            "deadline_ms": 100,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    timestamp = str(int(time.time()))
    headers = {
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": "one-use-nonce",
        "X-Robopark-Signature": body_signature(
            settings.secret_key, timestamp, "one-use-nonce", body
        ),
        "Content-Type": "application/json",
    }
    with TestClient(app) as client:
        first = client.post("/internal/v1/robot-payload", content=body, headers=headers)
        replay = client.post("/internal/v1/robot-payload", content=body, headers=headers)

    assert first.status_code == 503
    assert replay.status_code == 401


def test_result_authority_rechecks_requester_and_source(
    db_engine, db_session, seed_admin, seed_royal, monkeypatch
):
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=seed_admin.id,
        label="Mac",
        public_key_x963=b"\x04" + b"x" * 64,
        environment="test",
        instance_id="11111111-1111-1111-1111-111111111111",
        approved_at=datetime.now(UTC),
        approved_by_user_id=seed_admin.id,
    )
    db_session.add(device)
    db_session.commit()
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_environment="test",
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    factory = sessionmaker(bind=db_engine, future=True)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)
    assert _job_authority_valid(factory, settings, device.id, seed_royal.id, "YASADR00000000447")

    seed_royal.access_status = "rejected"
    db_session.commit()

    assert not _job_authority_valid(
        factory, settings, device.id, seed_royal.id, "YASADR00000000447"
    )
