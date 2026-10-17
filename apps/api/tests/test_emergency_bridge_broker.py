import asyncio
import base64
import concurrent.futures
import json
import math
import threading
import time
import zlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from conftest import role_id_for
from robopark_api.config import Settings
from robopark_api.emergency_bridge import (
    _candidate_device_ids,
    _decode_result_payload,
    _job_authority_valid,
    _validated_robot_payload_size,
    create_broker_app,
)
from robopark_api.models import (
    AccessStatus,
    AgentDevice,
    AuditLog,
    Park,
    TelegramAccount,
    User,
    UserPark,
)
from robopark_api.security import hash_password
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


class ReconnectReplySocket(Socket):
    def __init__(self, registry, channel):
        super().__init__()
        self.registry = registry
        self.channel = channel
        self.replacement = None

    async def send_json(self, value):
        await super().send_json(value)
        self.registry.remove(self.channel)

        async def reconnect():
            await asyncio.sleep(0.02)
            replacement = _channel(
                self.channel.device_id,
                1,
                generation="reconnected-generation",
            )
            replacement.capacity = 1
            replacement.websocket = ReplySocket(self.registry, replacement.device_id)
            self.replacement = replacement
            self.registry.replace(replacement)

        asyncio.create_task(reconnect())


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


def test_selector_distributes_equal_work_across_one_two_twelve_and_one_hundred_agents():
    async def scenario(size: int):
        registry = BrokerRegistry(max_connections=100)
        devices = [_channel(f"mac-{index:03d}", 20) for index in range(size)]
        for channel in devices:
            registry.replace(channel)
        selected = []
        for _ in range(size * 2):
            channel = registry.choose({item.device_id for item in devices})
            assert channel is not None
            job = registry.new_pending(channel, "VIN")
            selected.append(channel.device_id)
            registry.finish(channel, job)
            job.future.cancel()
        assert set(selected) == {item.device_id for item in devices}
        assert max(selected.count(item.device_id) for item in devices) <= 3

    for size in (1, 2, 12, 100):
        asyncio.run(scenario(size))


def test_selector_respects_pause_capacity_load_and_aging():
    async def scenario():
        registry = BrokerRegistry(max_connections=100)
        paused = _channel("paused", 1)
        paused.paused = True
        saturated = _channel("saturated", 1, inflight=1)
        saturated.capacity = 1
        queued = _channel("queued", 1)
        queued.capacity = 1
        queued.queued = 1
        loaded = _channel("loaded", 5)
        loaded.reported_load = 1.0
        available = _channel("available", 20)
        for channel in (paused, saturated, queued, loaded, available):
            registry.replace(channel)
        assert (
            registry.choose({"paused", "saturated", "queued", "loaded", "available"}) is available
        )

        # An agent that has waited is eventually selected despite moderate latency.
        available.virtual_finish = 10_000
        loaded.reported_load = 0.5
        loaded.last_selected = time.monotonic() - 120
        assert registry.choose({"loaded", "available"}) is loaded

    asyncio.run(scenario())


def test_selector_rejects_critical_or_saturated_resource_hints_and_throttles_serious_heat():
    registry = BrokerRegistry(max_connections=100)
    critical = _channel("critical", 1)
    critical.thermal_state = "critical"
    saturated = _channel("saturated-resource", 1)
    saturated.cpu_percent = 99.0
    serious = _channel("serious", 1)
    serious.capacity = 4
    serious.thermal_state = "serious"
    cool = _channel("cool", 20)
    cool.capacity = 4
    for channel in (critical, saturated, serious, cool):
        registry.replace(channel)

    assert registry.choose({"critical", "saturated-resource", "serious", "cool"}) is cool
    cool.inflight = 2
    assert registry.choose({"critical", "saturated-resource", "serious", "cool"}) is serious
    serious.inflight = 1
    assert registry.choose({"critical", "saturated-resource", "serious"}) is None


def test_status_projects_bounded_resources_freshness_and_observed_response_speed(monkeypatch):
    now = 500.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    registry = BrokerRegistry(max_connections=100)
    channel = _channel("mac", 20)
    channel.cpu_percent = 42.5
    channel.memory_percent = 61.0
    channel.thermal_state = "fair"
    channel.response_latency_ms = 250.0
    channel.response_throughput_mbps = 8.0
    channel.last_upstream_success = now - 4
    channel.last_heartbeat = now
    registry.replace(channel)

    projected = registry.status()["devices"][0]

    assert projected["fresh"] is True
    assert projected["cpu_percent"] == 42.5
    assert projected["memory_percent"] == 61.0
    assert projected["thermal_state"] == "fair"
    assert projected["response_latency_ms"] == 250.0
    assert projected["throughput_mbps"] == 8.0
    assert projected["last_success_age_seconds"] == 4.0

    now = 531.0
    stale = registry.status()["devices"][0]
    assert stale["fresh"] is False
    assert stale["status"] == "offline"
    assert stale["cpu_percent"] is None
    assert stale["throughput_mbps"] is None

    now = 565.0
    channel.last_heartbeat = now
    expired_measurement = registry.status()["devices"][0]
    assert expired_measurement["fresh"] is True
    assert expired_measurement["response_latency_ms"] is None
    assert expired_measurement["throughput_mbps"] is None
    assert expired_measurement["last_success_age_seconds"] == 69.0


def test_status_distinguishes_saturated_zero_capacity_and_corporate_unavailable():
    registry = BrokerRegistry(max_connections=100)
    channel = _channel("mac", 20, inflight=2)
    registry.replace(channel)
    saturated = registry.status()["devices"][0]
    assert saturated["status"] == "busy"
    assert saturated["ready"] is False
    channel.inflight = 0
    channel.capacity = 0
    assert registry.choose({"mac"}) is None
    assert registry.reserve_queue_slot(channel) is False
    overloaded = registry.status()["devices"][0]
    assert overloaded["status"] == "busy"
    assert overloaded["ready"] is False
    channel.ready = False
    channel.supports = {"managed_probes"}
    channel.probe_available = False
    assert registry.status()["devices"][0]["status"] == "corporate_unavailable"
    channel.capacity = 2
    channel.ready = True
    channel.probe_available = True
    assert registry.status()["devices"][0]["status"] == "ready"
    assert registry.status()["devices"][0]["ready"] is True


def test_readiness_probe_does_not_count_as_an_actual_emergency_response(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    registry = BrokerRegistry(max_connections=100)
    channel = _channel("mac", 20)
    channel.supports = {"managed_probes"}
    channel.probe_available = True
    channel.last_upstream_success = now - 90
    registry.replace(channel)
    assert registry.reserve_probe(channel, now=now) is True

    assert (
        registry.finish_probe(
            channel,
            probe_id=channel.probe_id or "",
            ready=True,
            latency_ms=12,
            now=now + 1,
        )
        is True
    )

    assert channel.last_upstream_success == now - 90


def test_success_observes_response_latency_throughput_and_compression_savings(monkeypatch):
    now = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    registry = BrokerRegistry(max_connections=100)
    channel = _channel("mac", 20)

    registry.record_success(
        channel,
        encoded_bytes=250_000,
        decoded_bytes=1_000_000,
        duration_seconds=0.25,
        now=now,
    )

    assert channel.response_latency_ms == 250.0
    assert channel.response_throughput_mbps == 8.0
    assert channel.compression_saved_bytes == 750_000


def test_candidate_filter_keeps_only_same_park_operator_allowed_for_mechanic(
    db_engine, db_session, monkeypatch
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    owners = []
    devices = []
    for index in range(5):
        owner = User(
            username=f"vin-scope-owner-{index}",
            password_hash=hash_password("secret"),
            role_id=role_id_for(db_session, "operator"),
            access_status=AccessStatus.approved.value,
            is_active=True,
        )
        db_session.add(owner)
        db_session.flush()
        device = AgentDevice(
            id=str(uuid4()),
            owner_user_id=owner.id,
            label=f"Mac {index}",
            public_key_x963=b"\x04" + bytes([index]) * 64,
            environment="production",
            instance_id=str(settings.robopark_instance_id),
            approved_at=datetime.now(UTC),
            approved_by_user_id=owner.id,
        )
        db_session.add(device)
        owners.append(owner)
        devices.append(device)
    mechanic = User(
        username="gateway-candidate-mechanic",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    shared_park = Park(name="Candidate shared", tag="Candidate-shared", is_active=True)
    db_session.add_all([mechanic, shared_park])
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=mechanic.id, park_id=shared_park.id),
            UserPark(user_id=owners[-1].id, park_id=shared_park.id),
        ]
    )
    db_session.commit()
    allowed_owner_id = owners[-1].id
    monkeypatch.setattr(
        emergency_scope,
        "vin_allowed_for_user",
        lambda _db, user, _vin: user.id in {mechanic.id, allowed_owner_id},
    )

    eligible = _candidate_device_ids(
        sessionmaker(bind=db_engine, future=True),
        settings,
        mechanic.id,
        "YASADR00000000447",
        {device.id for device in devices},
    )

    assert eligible == {devices[-1].id}

    devices[-1].revoked_at = datetime.now(UTC)
    db_session.commit()
    assert (
        _candidate_device_ids(
            sessionmaker(bind=db_engine, future=True),
            settings,
            mechanic.id,
            "YASADR00000000447",
            {device.id for device in devices},
        )
        == set()
    )


def test_managed_probe_budget_is_global_bounded_and_success_postpones_probe(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    registry = BrokerRegistry(max_connections=100)
    channels = [_channel(f"mac-{index}", 10) for index in range(4)]
    for channel in channels:
        channel.supports = {"managed_probes"}
        channel.probe_available = True
        channel.last_upstream_success = now - 121
        registry.replace(channel)

    assert registry.reserve_probe(channels[0], now=now) is True
    assert registry.reserve_probe(channels[1], now=now) is True
    assert registry.reserve_probe(channels[2], now=now) is False
    registry.record_success(channels[0], encoded_bytes=100, decoded_bytes=250, now=now)
    assert registry.probe_due(channels[0], now=now + 59) is False
    assert registry.probe_due(channels[0], now=now + 60) is True


def test_raw_deflate_result_decodes_with_exact_bounds_and_schema():
    vin = "YASADR00000000447"
    payload = {"vin": vin, "timestamp": 1_700_000_000_000, "isOnline": True}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    compressor = zlib.compressobj(wbits=-15)
    encoded = compressor.compress(raw) + compressor.flush()
    message = {
        "ok": True,
        "encoding": "deflate-raw",
        "payload_base64": base64.b64encode(encoded).decode(),
        "payload_bytes": len(raw),
        "encoded_bytes": len(encoded),
    }

    decoded = _decode_result_payload(message, vin, now_ms=2_000_000_000_000)
    assert decoded == (payload, len(encoded), len(raw))


def test_raw_deflate_rejects_corruption_bombs_and_size_lies():
    vin = "YASADR00000000447"
    raw = json.dumps({"vin": vin, "timestamp": 1_700_000_000_000}).encode()
    compressor = zlib.compressobj(wbits=-15)
    encoded = compressor.compress(raw) + compressor.flush()

    def envelope(data: bytes, decoded_size: int):
        return {
            "ok": True,
            "encoding": "deflate-raw",
            "payload_base64": base64.b64encode(data).decode(),
            "payload_bytes": decoded_size,
            "encoded_bytes": len(data),
        }

    assert _decode_result_payload(envelope(encoded[:-1], len(raw)), vin) is None
    assert _decode_result_payload(envelope(encoded, len(raw) + 1), vin) is None

    bomb_raw = b"{" + b'"x":"' + b"a" * (2 * 1024 * 1024) + b'"}'
    compressor = zlib.compressobj(wbits=-15)
    bomb = compressor.compress(bomb_raw) + compressor.flush()
    assert _decode_result_payload(envelope(bomb, len(bomb_raw)), vin) is None


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


def test_singleflight_shares_only_the_same_gateway_owner_scope():
    async def scenario():
        registry = BrokerRegistry(max_connections=100)
        leader, is_leader = registry.begin_request((11,), "VIN1")
        follower, is_follower_leader = registry.begin_request((11,), "VIN1")
        other, is_other_leader = registry.begin_request((12,), "VIN1")
        assert is_leader is True
        assert is_follower_leader is False
        assert follower is leader
        assert is_other_leader is True
        assert other is not leader
        registry.finish_request((11,), "VIN1", result=({"value": 1}, "mac"))
        assert await follower == ({"value": 1}, "mac")
        registry.finish_request((12,), "VIN1", result=({"value": 2}, "other-mac"))
        assert await other == ({"value": 2}, "other-mac")

    asyncio.run(scenario())


def test_shared_vin_cache_keeps_source_for_authority_recheck():
    registry = BrokerRegistry(max_connections=100, cache_ttl_seconds=3)
    registry.shared_cache_put((11,), "VIN1", {"value": 1}, source_device_id="source-mac")

    assert registry.shared_cache_get((11,), "VIN1") == ({"value": 1}, "source-mac")
    assert registry.shared_cache_get((12,), "VIN1") is None
    assert registry.shared_cache_get((11,), "VIN2") is None


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


def test_request_admission_bounds_global_rate_and_per_requester_concurrency(monkeypatch):
    now = 500.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    registry = BrokerRegistry(max_connections=100)

    assert all(registry.admit_request(7, now=now) for _ in range(60))
    assert all(registry.admit_request(7, now=now) for _ in range(4))
    assert registry.admit_request(7, now=now) is False
    assert registry.admit_request(8, now=now) is True
    for _ in range(64):
        registry.release_request(7)
    assert registry.admit_request(7, now=now) is True
    assert registry.metrics["rejected"] == 1


def test_sixty_requests_from_one_requester_drain_through_two_mac_slots():
    async def scenario():
        registry = BrokerRegistry(max_connections=1, max_inflight_per_device=2)
        channel = _channel("one", 1)
        channel.capacity = 2
        registry.replace(channel)
        peak_inflight = 0
        completed = 0

        async def request(index: int) -> None:
            nonlocal completed, peak_inflight
            assert registry.admit_request(7)
            try:
                selected = await registry.wait_for_available(
                    {channel.device_id},
                    excluded_device_ids=set(),
                    excluded_generations=set(),
                    deadline=time.monotonic() + 5,
                    wait_through_empty=False,
                )
                assert selected is channel
                assert registry.reserve_queue_slot(channel)
                registry.release_queue_slot(channel)
                assert registry.channel_available(channel)
                job = registry.new_pending(channel, f"VIN-{index}")
                peak_inflight = max(peak_inflight, channel.inflight)
                await asyncio.sleep(0)
                registry.finish(channel, job)
                job.future.cancel()
                completed += 1
            finally:
                registry.release_request(7)

        await asyncio.gather(*(request(index) for index in range(60)))
        assert completed == 60
        assert peak_inflight == 2
        assert channel.inflight == channel.queued == 0
        assert registry.pending == {}
        assert registry._active_requests == {}

    asyncio.run(scenario())


def test_corporate_upstream_budget_caps_hundred_unique_dispatches_at_eight(monkeypatch):
    now = 700.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    registry = BrokerRegistry(max_connections=100, upstream_reads_per_second=8)
    channels = [_channel(f"mac-{index:03d}", 10) for index in range(100)]

    granted = [
        channel.device_id
        for channel in channels
        if registry.try_reserve_upstream(channel, kind="job", now=now)
    ]

    assert len(granted) == 8
    assert registry.metrics["upstream_dispatches"] == 8
    assert registry.metrics["upstream_backpressure"] == 92
    assert sum(channel.upstream_dispatches for channel in channels) == 8


def test_capacity_waiters_are_fifo_even_when_newcomer_runs_before_awakened_waiter():
    async def scenario():
        registry = BrokerRegistry(max_connections=1, max_inflight_per_device=1)
        channel = _channel("mac", 1, inflight=1)
        channel.capacity = 1
        registry.replace(channel)
        order = []
        release_oldest = asyncio.Event()

        async def claim(label: str):
            selected = await registry.wait_for_available(
                {channel.device_id},
                excluded_device_ids=set(),
                excluded_generations=set(),
                deadline=time.monotonic() + 1,
                wait_through_empty=False,
            )
            assert selected is channel
            assert registry.reserve_queue_slot(selected)
            order.append(label)
            if label == "oldest":
                await release_oldest.wait()
            registry.release_queue_slot(selected)

        oldest = asyncio.create_task(claim("oldest"))
        await asyncio.sleep(0)
        channel.inflight = 0
        registry.capacity_changed()

        loop = asyncio.get_running_loop()
        prior_factory = loop.get_task_factory()
        loop.set_task_factory(asyncio.eager_task_factory)
        try:
            newcomer = asyncio.create_task(claim("newcomer"))
        finally:
            loop.set_task_factory(prior_factory)

        await asyncio.sleep(0)
        assert order == ["oldest"]
        release_oldest.set()
        await asyncio.gather(oldest, newcomer)
        assert order == ["oldest", "newcomer"]

    asyncio.run(scenario())


def test_waiting_for_first_slot_survives_sole_mac_disconnect():
    async def scenario():
        registry = BrokerRegistry(max_connections=1, max_inflight_per_device=1)
        channel = _channel("mac", 1, inflight=1)
        channel.capacity = 1
        registry.replace(channel)
        waiting = asyncio.create_task(
            registry.wait_for_available(
                {"mac"},
                excluded_device_ids=set(),
                excluded_generations=set(),
                deadline=time.monotonic() + 1,
                wait_through_empty=False,
            )
        )
        await asyncio.sleep(0)
        registry.remove(channel)
        await asyncio.sleep(0.02)
        assert not waiting.done()
        replacement = _channel("mac", 1)
        replacement.capacity = 1
        registry.replace(replacement)
        assert await waiting is replacement
        assert not registry._capacity_waiters

    asyncio.run(scenario())


def test_cancelling_oldest_capacity_waiter_unblocks_next_waiter():
    async def scenario():
        registry = BrokerRegistry(max_connections=1, max_inflight_per_device=1)
        channel = _channel("mac", 1, inflight=1)
        channel.capacity = 1
        registry.replace(channel)
        deadline = time.monotonic() + 1
        oldest = asyncio.create_task(
            registry.wait_for_available(
                {channel.device_id},
                excluded_device_ids=set(),
                excluded_generations=set(),
                deadline=deadline,
                wait_through_empty=False,
            )
        )
        next_waiter = asyncio.create_task(
            registry.wait_for_available(
                {channel.device_id},
                excluded_device_ids=set(),
                excluded_generations=set(),
                deadline=deadline,
                wait_through_empty=False,
            )
        )
        await asyncio.sleep(0)
        oldest.cancel()
        with pytest.raises(asyncio.CancelledError):
            await oldest
        channel.inflight = 0
        registry.capacity_changed()
        assert await next_waiter is channel

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


@pytest.mark.parametrize("capacity", [1, 2])
def test_silent_first_agent_reserves_time_for_second_attempt(
    db_engine, db_session, seed_admin, capacity
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    devices = []
    for index, label in enumerate(("Silent", "Responsive"), start=1):
        device = AgentDevice(
            id=str(uuid4()),
            owner_user_id=seed_admin.id,
            label=label,
            public_key_x963=b"\x04" + bytes([index]) * 64,
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
    silent.capacity = responsive.capacity = capacity
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
    assert silent.queued == responsive.queued == 0
    assert silent.inflight == responsive.inflight == 0
    assert silent.websocket.sent[0]["timeout_ms"] <= 400
    assert elapsed < 0.8


def test_single_mac_reconnects_with_new_generation_during_retry_deadline(
    db_engine, db_session, seed_admin
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
        label="Reconnecting Mac",
        public_key_x963=b"\x04" + b"r" * 64,
        environment="production",
        instance_id=str(settings.robopark_instance_id),
        approved_at=datetime.now(UTC),
        approved_by_user_id=seed_admin.id,
    )
    db_session.add(device)
    db_session.commit()
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    registry = app.state.registry
    first = _channel(device.id, 1, generation="first-generation")
    first.capacity = 1
    reconnecting_socket = ReconnectReplySocket(registry, first)
    first.websocket = reconnecting_socket
    registry.replace(first)
    body = json.dumps(
        {
            "vin": "YASADR00000000447",
            "requester_user_id": seed_admin.id,
            "attempt_budget": 2,
            "deadline_ms": 1000,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    timestamp = str(int(time.time()))
    nonce = "single-mac-reconnect"
    headers = {
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(settings.secret_key, timestamp, nonce, body),
        "Content-Type": "application/json",
    }

    with TestClient(app) as client:
        response = client.post("/internal/v1/robot-payload", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["device_id"] == device.id
    assert response.json()["attempts_used"] == 2
    assert len(first.websocket.sent) == 1
    assert reconnecting_socket.replacement is not None
    assert len(reconnecting_socket.replacement.websocket.sent) == 1
    assert registry.pending == {}
    assert registry.loads == {}


def test_device_singleflight_denial_does_not_send_replacement_job(
    db_engine, db_session, monkeypatch
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    owner = User(
        username="singleflight-owner",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    leader_user = User(
        username="singleflight-leader",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    follower_user = User(
        username="singleflight-follower",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    park = Park(name="Singleflight", tag="Singleflight", is_active=True)
    db_session.add_all([owner, leader_user, follower_user, park])
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=owner.id, park_id=park.id),
            UserPark(user_id=leader_user.id, park_id=park.id),
            UserPark(user_id=follower_user.id, park_id=park.id),
        ]
    )
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=owner.id,
        label="Shared Mac",
        public_key_x963=b"\x04" + b"x" * 64,
        environment="production",
        instance_id=str(settings.robopark_instance_id),
        approved_at=datetime.now(UTC),
        approved_by_user_id=owner.id,
    )
    db_session.add(device)
    db_session.commit()
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    registry = app.state.registry
    channel = _channel(device.id, 1)
    socket = GateReplySocket(registry, device.id)
    channel.websocket = socket
    registry.replace(channel)
    follower_waiting = threading.Event()
    original_begin_request = registry.begin_request

    def tracked_begin_request(*args, **kwargs):
        result = original_begin_request(*args, **kwargs)
        if result[1] is False:
            follower_waiting.set()
        return result

    registry.begin_request = tracked_begin_request

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

    first_body, first_headers = signed_request(leader_user.id, "singleflight-leader")
    second_body, second_headers = signed_request(follower_user.id, "singleflight-follower")
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
        follower_user.access_status = "rejected"
        db_session.commit()
        socket.release.set()
        first_response = first.result(timeout=2)
        second_response = second.result(timeout=2)

    assert first_response.status_code == 200
    assert second_response.status_code == 503
    assert second_response.json()["detail"] == "no_eligible_agent"
    assert len(socket.sent) == 1


def test_websocket_authenticates_registered_p256_device(db_engine, db_session, seed_admin):
    seed_admin.tracker_login = "admin.tracker"
    db_session.commit()
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
        assert ready["owner_user_id"] == seed_admin.id
        assert ready["owner_tracker_login"] == "admin.tracker"
        assert ready["max_inflight"] == 2
        assert ready["supports"] == ["raw_deflate", "managed_probes", "heartbeat_ack"]
        ws.send_json(
            {
                "type": "heartbeat",
                "generation": ready["generation"],
                "ready": True,
                "capacity": 1,
                "active_jobs": 0,
                "power": [],
            }
        )
        ws.send_json(
            {
                "type": "heartbeat",
                "generation": ready["generation"],
                "ready": True,
                "capacity": 9,
                "active_jobs": 0,
            }
        )
        ws.send_json(
            {
                "type": "heartbeat",
                "generation": ready["generation"],
                "ready": True,
                "latency_ms": 20,
                "capacity": 2,
                "active_jobs": 0,
                "load": 0.1,
                "paused": False,
                "power": "ac",
                "supports": ["raw_deflate", "managed_probes"],
                "probe_available": True,
                "cpu_percent": 42.5,
                "memory_percent": 61,
                "thermal_state": "fair",
            }
        )
        assert ws.receive_json() == {
            "type": "heartbeat_ack",
            "generation": ready["generation"],
        }
        probe = ws.receive_json()
        assert probe["type"] == "probe"
        assert probe["operation"] == "emergency_readiness"
        assert probe["timeout_ms"] == 10_000
        channel = app.state.registry.channels[device.id]
        assert channel.cpu_percent == 42.5
        assert channel.memory_percent == 61.0
        assert channel.thermal_state == "fair"
        ws.send_json(
            {
                "type": "probe_result",
                "generation": ready["generation"],
                "probe_id": probe["probe_id"],
                "ready": True,
                "latency_ms": 18,
            }
        )
        ws.send_json(
            {
                "type": "heartbeat",
                "generation": ready["generation"],
                "ready": True,
                "capacity": 0,
                "active_jobs": 1,
                "cpu_percent": 96,
                "load": 1.0,
            }
        )
        assert ws.receive_json() == {
            "type": "heartbeat_ack",
            "generation": ready["generation"],
        }
        deadline = time.monotonic() + 2
        while channel.cpu_percent != 96 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert channel.cpu_percent == 96
        assert channel.capacity == 0
        assert channel.reported_active_jobs == 1  # Existing work drains at zero capacity.
        assert app.state.registry.choose({device.id}) is None
        assert app.state.registry.status()["devices"][0]["status"] == "busy"
        device.revoked_at = datetime.now(UTC)
        db_session.commit()
        ws.send_json(
            {
                "type": "heartbeat",
                "generation": ready["generation"],
                "ready": True,
                "capacity": 1,
                "active_jobs": 0,
            }
        )
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_emergency_websocket_reopens_after_same_app_lifespan_restart(
    db_engine, db_session, seed_admin
):
    private_key = ec.generate_private_key(ec.SECP256R1())
    device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=seed_admin.id,
        label="Restarted Emergency Mac",
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

    for _ in range(2):
        with (
            TestClient(app) as client,
            client.websocket_connect("/emergency/bridge/ws") as ws,
        ):
            challenge = ws.receive_json()
            signed = (
                f"robopark-bridge-v1\n{challenge['instance_id']}\n"
                f"{challenge['environment']}\n{challenge['nonce']}"
            ).encode()
            signature = private_key.sign(signed, ec.ECDSA(hashes.SHA256()))
            ws.send_json(
                {
                    "type": "auth",
                    "device_id": device.id,
                    "signature": base64.b64encode(signature).decode(),
                }
            )
            assert ws.receive_json()["type"] == "ready"


def test_legacy_pending_device_of_approved_account_is_trusted_on_bridge_login(
    db_engine, db_session, seed_admin
):
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
        ready = ws.receive_json()
        assert ready["type"] == "ready"

    db_session.expire_all()
    trusted = db_session.get(AgentDevice, device.id)
    assert trusted.approved_at is not None
    assert trusted.approved_by_user_id == seed_admin.id
    audit_entry = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "agent_device.auto_trusted", AuditLog.target_id == device.id
        )
    )
    assert audit_entry is not None
    assert audit_entry.actor_user_id == seed_admin.id


def test_expired_managed_probe_is_released_and_counted(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    registry = BrokerRegistry(max_connections=100)
    channel = _channel("mac", 10)
    channel.supports = {"managed_probes"}
    channel.probe_available = True
    registry.replace(channel)
    assert registry.reserve_probe(channel, now=now)
    assert channel.probe_inflight

    assert registry.probe_due(channel, now=now + 11) is False
    assert channel.probe_inflight is False
    assert channel.probes_failed == 1


def test_probe_scheduler_order_serves_one_hundred_agents_within_budget(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    registry = BrokerRegistry(max_connections=100)
    channels = [_channel(f"mac-{index:03d}", 10) for index in range(100)]
    for channel in channels:
        channel.supports = {"managed_probes"}
        channel.probe_available = True
        channel.last_upstream_success = now - 121
        registry.replace(channel)

    served = set()
    for tick in range(101):
        tick_now = now + tick * 0.5
        for channel in registry.due_probe_channels(now=tick_now):
            if not registry.reserve_probe(channel, now=tick_now):
                continue
            served.add(channel.device_id)
            assert registry.finish_probe(
                channel,
                probe_id=channel.probe_id or "",
                ready=True,
                latency_ms=10,
                now=tick_now,
            )

    assert served == {channel.device_id for channel in channels}


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


def test_private_pool_status_is_filtered_for_owner_and_aggregates_without_payloads(
    db_engine, db_session, seed_admin, seed_royal
):
    settings = Settings(
        _env_file=None,
        database_url=str(db_engine.url),
        secret_key="bridge-secret",
        emergency_bridge_enabled=True,
        robopark_instance_id="11111111-1111-1111-1111-111111111111",
    )
    devices = []
    for index, (owner, label) in enumerate(
        ((seed_admin, "Admin Mac"), (seed_royal, "Royal Mac")), start=1
    ):
        device = AgentDevice(
            id=str(uuid4()),
            owner_user_id=owner.id,
            label=label,
            public_key_x963=b"\x04" + bytes([index]) * 64,
            environment="production",
            instance_id=str(settings.robopark_instance_id),
            approved_at=datetime.now(UTC),
            approved_by_user_id=owner.id,
        )
        db_session.add(device)
        devices.append(device)
    db_session.add(
        TelegramAccount(
            user_id=seed_admin.id,
            telegram_user_id=900001,
            display_name="Admin Person",
        )
    )
    db_session.commit()
    app = create_broker_app(sessionmaker(bind=db_engine, future=True), settings)
    channel = _channel(devices[0].id, 12)
    channel.owner_user_id = seed_admin.id
    channel.requests = 4
    channel.completed = 3
    channel.encoded_bytes = 100
    channel.decoded_bytes = 250
    channel.upstream_dispatches = 3
    channel.upstream_backpressure = 1
    app.state.registry.replace(channel)

    body = json.dumps(
        {"requester_user_id": seed_admin.id}, separators=(",", ":"), sort_keys=True
    ).encode()
    timestamp = str(int(time.time()))
    nonce = "pool-status"
    headers = {
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(settings.secret_key, timestamp, nonce, body),
        "Content-Type": "application/json",
    }
    with TestClient(app) as client:
        response = client.post("/internal/v1/pool-status", content=body, headers=headers)

    assert response.status_code == 200
    result = response.json()
    assert result["scope"] == "all"
    assert {item["label"] for item in result["devices"]} == {"Admin Mac", "Royal Mac"}
    assert result["summary"]["connected"] == 1
    assert result["summary"]["requests"] == 4
    assert result["summary"]["encoded_bytes"] == 100
    assert result["summary"]["upstream_dispatches"] == 3
    assert result["summary"]["upstream_backpressure"] == 1
    admin_projection = next(item for item in result["devices"] if item["id"] == devices[0].id)
    assert admin_projection["owner_username"] == seed_admin.username
    assert admin_projection["owner_display_name"] == "Admin Person"
    assert "payload" not in json.dumps(result).lower()

    operator = User(
        username="pool-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    operator_device = AgentDevice(
        id=str(uuid4()),
        owner_user_id=operator.id,
        label="Operator Mac",
        public_key_x963=b"\x04" + b"x" * 64,
        environment="production",
        instance_id=str(settings.robopark_instance_id),
        approved_at=datetime.now(UTC),
        approved_by_user_id=operator.id,
    )
    db_session.add(operator_device)
    db_session.commit()
    operator_body = json.dumps(
        {"requester_user_id": operator.id}, separators=(",", ":"), sort_keys=True
    ).encode()
    operator_nonce = "pool-status-operator"
    operator_timestamp = str(int(time.time()))
    operator_headers = {
        "X-Robopark-Timestamp": operator_timestamp,
        "X-Robopark-Nonce": operator_nonce,
        "X-Robopark-Signature": body_signature(
            settings.secret_key, operator_timestamp, operator_nonce, operator_body
        ),
        "Content-Type": "application/json",
    }
    with TestClient(app) as client:
        own_response = client.post(
            "/internal/v1/pool-status", content=operator_body, headers=operator_headers
        )
    assert own_response.status_code == 200
    assert own_response.json()["scope"] == "own"
    assert [item["id"] for item in own_response.json()["devices"]] == [operator_device.id]
    serialized = json.dumps(own_response.json())
    assert seed_admin.username not in serialized
    assert "Admin Person" not in serialized


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
    assert not _job_authority_valid(
        factory, settings, device.id, seed_royal.id, "YASADR00000000447"
    )
    assert _job_authority_valid(factory, settings, device.id, seed_admin.id, "YASADR00000000447")

    seed_admin.access_status = "rejected"
    db_session.commit()

    assert not _job_authority_valid(
        factory, settings, device.id, seed_admin.id, "YASADR00000000447"
    )
