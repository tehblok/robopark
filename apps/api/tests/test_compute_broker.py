import asyncio
import base64
import hashlib
import time

import pytest

from robopark_api.services import compute_broker
from robopark_api.services.compute_broker import (
    ComputeChannel,
    ComputeRegistry,
    validate_result,
)


class Socket:
    def __init__(self):
        self.sent = []

    async def send_json(self, value):
        self.sent.append(value)


def test_relay_admission_is_one_use_owner_scoped_and_expires_without_leaking_capacity():
    now = [100.0]
    pool = compute_broker.RelayAdmissionPool(
        now=lambda: now[0],
        token_factory=lambda: "relay-ticket",
    )

    ticket = pool.admit(
        requester_user_id=7,
        payload_bytes=400_000,
        payload_sha256="a" * 64,
        worker_available=True,
    )
    assert ticket.token == "relay-ticket"
    assert pool.status()["relay_active"] == 1

    with pytest.raises(PermissionError, match="relay_ticket_foreign"):
        pool.claim("relay-ticket", requester_user_id=8, payload_bytes=400_000)
    claimed = pool.claim("relay-ticket", requester_user_id=7, payload_bytes=400_000)
    assert claimed.payload_sha256 == "a" * 64
    assert (
        pool.authorize_dispatch(
            "relay-ticket",
            requester_user_id=7,
            payload_bytes=400_000,
            payload_sha256="a" * 64,
        )
        is claimed
    )
    with pytest.raises(ValueError, match="relay_payload_hash_mismatch"):
        pool.authorize_dispatch(
            "relay-ticket",
            requester_user_id=7,
            payload_bytes=400_000,
            payload_sha256="0" * 64,
        )
    with pytest.raises(LookupError, match="relay_ticket_consumed"):
        pool.claim("relay-ticket", requester_user_id=7, payload_bytes=400_000)

    pool.complete("relay-ticket", requester_user_id=7, payload_bytes=400_000)
    assert pool.status()["relay_active"] == 0
    assert pool.status()["relay_payload_bytes"] == 400_000

    second = pool.admit(
        requester_user_id=7,
        payload_bytes=400_000,
        payload_sha256="b" * 64,
        worker_available=True,
    )
    now[0] += 31
    with pytest.raises(LookupError, match="relay_ticket_expired"):
        pool.claim(second.token, requester_user_id=7, payload_bytes=400_000)
    assert pool.status()["relay_active"] == 0


def test_relay_admission_enforces_worker_global_requester_and_rolling_traffic_budget():
    now = [100.0]
    tokens = iter(("one", "two", "three", "four"))
    pool = compute_broker.RelayAdmissionPool(
        max_active=2,
        max_requester_active=1,
        budget_bytes_per_minute=12_000_000,
        now=lambda: now[0],
        token_factory=lambda: next(tokens),
    )

    with pytest.raises(OverflowError, match="relay_unavailable"):
        pool.admit(
            requester_user_id=1,
            payload_bytes=300_000,
            payload_sha256="a" * 64,
            worker_available=False,
        )
    first = pool.admit(
        requester_user_id=1,
        payload_bytes=300_000,
        payload_sha256="a" * 64,
        worker_available=True,
    )
    with pytest.raises(OverflowError, match="relay_requester_busy"):
        pool.admit(
            requester_user_id=1,
            payload_bytes=300_000,
            payload_sha256="b" * 64,
            worker_available=True,
        )
    second = pool.admit(
        requester_user_id=2,
        payload_bytes=300_000,
        payload_sha256="b" * 64,
        worker_available=True,
    )
    with pytest.raises(OverflowError, match="relay_busy"):
        pool.admit(
            requester_user_id=3,
            payload_bytes=300_000,
            payload_sha256="c" * 64,
            worker_available=True,
        )

    pool.cancel(first.token, requester_user_id=1)
    pool.claim(second.token, requester_user_id=2, payload_bytes=300_000)
    pool.complete(second.token, requester_user_id=2, payload_bytes=300_000)
    with pytest.raises(OverflowError, match="relay_budget_exhausted"):
        pool.admit(
            requester_user_id=3,
            payload_bytes=900_000,
            payload_sha256="c" * 64,
            worker_available=True,
        )
    assert pool.status()["relay_rejected"] == 4
    assert pool.status()["relay_budget_reserved_bytes"] > 0

    now[0] += 61
    admitted = pool.admit(
        requester_user_id=3,
        payload_bytes=900_000,
        payload_sha256="c" * 64,
        worker_available=True,
    )
    assert admitted.token == "three"


def test_claimed_relay_ignores_client_cancel_and_survives_admission_expiry():
    now = [100.0]
    tokens = iter(("one", "two"))
    pool = compute_broker.RelayAdmissionPool(
        now=lambda: now[0],
        token_factory=lambda: next(tokens),
    )
    ticket = pool.admit(
        requester_user_id=1,
        payload_bytes=300_000,
        payload_sha256="a" * 64,
        worker_available=True,
    )
    pool.claim(ticket.token, requester_user_id=1, payload_bytes=300_000)

    assert pool.cancel(ticket.token, requester_user_id=1) is False
    now[0] += 31
    assert (
        pool.authorize_dispatch(
            ticket.token,
            requester_user_id=1,
            payload_bytes=300_000,
            payload_sha256="a" * 64,
        )
        is ticket
    )
    with pytest.raises(OverflowError, match="relay_requester_busy"):
        pool.admit(
            requester_user_id=1,
            payload_bytes=300_000,
            payload_sha256="b" * 64,
            worker_available=True,
        )

    pool.complete(ticket.token, requester_user_id=1, payload_bytes=300_000)
    assert pool.status()["relay_active"] == 0


def test_server_abort_releases_claimed_relay_and_claim_timeout_bounds_orphans():
    now = [100.0]
    tokens = iter(("one", "two", "three"))
    pool = compute_broker.RelayAdmissionPool(
        now=lambda: now[0],
        token_factory=lambda: next(tokens),
    )
    ticket = pool.admit(
        requester_user_id=1,
        payload_bytes=300_000,
        payload_sha256="a" * 64,
        worker_available=True,
    )
    pool.claim(ticket.token, requester_user_id=1, payload_bytes=300_000)
    assert pool.abort(ticket.token, requester_user_id=1) is True
    assert pool.status()["relay_active"] == 0

    orphan = pool.admit(
        requester_user_id=1,
        payload_bytes=300_000,
        payload_sha256="b" * 64,
        worker_available=True,
    )
    pool.claim(orphan.token, requester_user_id=1, payload_bytes=300_000)
    now[0] += compute_broker.RELAY_CLAIM_TTL_SECONDS + 1
    replacement = pool.admit(
        requester_user_id=1,
        payload_bytes=300_000,
        payload_sha256="c" * 64,
        worker_available=True,
    )
    assert replacement.token == "three"


def channel(device_id: str, *, generation: str | None = None) -> ComputeChannel:
    return ComputeChannel(
        device_id=device_id,
        owner_user_id=1,
        websocket=Socket(),
        generation=generation or f"generation-{device_id}",
        ready=True,
        supports={"image_resize_v1", "csv_render_v1"},
    )


def test_scheduler_handles_zero_two_and_one_hundred_devices():
    async def scenario(size: int):
        registry = ComputeRegistry()
        assert registry.choose("image_resize_v1") is None
        devices = [channel(f"mac-{index:03d}") for index in range(size)]
        for item in devices:
            registry.replace(item)
        selected = []
        for _ in range(size):
            item = registry.choose("image_resize_v1")
            assert item is not None
            lease = registry.new_lease(
                item,
                operation="image_resize_v1",
                payload_bytes=b"{}",
                requester_user_id=7,
                deadline_monotonic=time.monotonic() + 20,
            )
            selected.append(item.device_id)
            registry.finish(item, lease)
            lease.future.cancel()
        assert set(selected) == {item.device_id for item in devices}

    for size in (2, 100):
        asyncio.run(scenario(size))


def test_corporate_scheduler_requires_owner_and_service_capability():
    registry = ComputeRegistry()
    inventory = channel("inventory")
    inventory.owner_user_id = 7
    inventory.supports.update({"corporate_read_v1", "corporate_inventory_read_v1"})
    wrong_owner = channel("wrong-owner")
    wrong_owner.owner_user_id = 8
    wrong_owner.supports.update({"corporate_read_v1", "corporate_inventory_read_v1"})
    missing_service = channel("missing-service")
    missing_service.owner_user_id = 7
    missing_service.supports.add("corporate_read_v1")
    for item in (wrong_owner, missing_service, inventory):
        item.corporate_read_capacity = 2
        registry.replace(item)

    chosen = registry.choose(
        "corporate_read_v1",
        required_supports={"corporate_inventory_read_v1"},
        eligible_owner_ids=frozenset({7}),
    )

    assert chosen is inventory
    assert (
        registry.choose(
            "corporate_read_v1",
            required_supports={"corporate_tracker_read_v1"},
            eligible_owner_ids=frozenset({7}),
        )
        is None
    )


def test_io_lanes_are_independent_of_cpu_and_of_each_other():
    registry = ComputeRegistry()
    node = channel("busy-cpu")
    node.capacity = 0
    node.cpu_percent = 95
    node.corporate_read_capacity = 2
    node.corporate_write_capacity = 1
    node.supports.update({"corporate_read_v1", "corporate_write_v1"})
    registry.replace(node)
    assert registry.choose("image_resize_v1") is None
    assert registry.choose("corporate_read_v1") is node
    assert registry.choose("corporate_write_v1") is node
    node.reported_corporate_active_jobs = 2
    assert registry.choose("corporate_read_v1") is None
    assert registry.choose("corporate_write_v1") is node
    node.paused = True
    assert registry.choose("corporate_write_v1") is None


def test_route_history_and_inventory_denial_do_not_poison_other_operations():
    registry = ComputeRegistry()
    first, second = channel("first"), channel("second")
    for node in (first, second):
        node.corporate_read_capacity = 2
        node.supports.update(
            {"corporate_read_v1", "corporate_inventory_read_v1", "corporate_tracker_read_v1"}
        )
        registry.replace(node)
    registry.routing_health.observe("first", "tracker:issue_read", ok=True, elapsed=1)
    registry.routing_health.observe("second", "tracker:issue_read", ok=True, elapsed=0.1)
    assert registry.choose("corporate_read_v1", routing_key="tracker:issue_read") is second
    second.corporate_inventory_status = "denied"
    assert (
        registry.choose(
            "corporate_read_v1",
            routing_key="inventory:rover_card",
            required_supports={"corporate_inventory_read_v1"},
        )
        is first
    )
    assert registry.choose("corporate_read_v1", routing_key="tracker:issue_read") is second
    registry.routing_health.observe(
        "second", "tracker:issue_read", ok=False, elapsed=1, error="timeout"
    )
    assert registry.choose("corporate_read_v1", routing_key="tracker:issue_read") is first
    assert (
        registry.choose(
            "corporate_read_v1", routing_key="tracker:comments_read", excluded={"first"}
        )
        is second
    )


def test_scheduler_enforces_queue_memory_and_per_requester_bounds():
    registry = ComputeRegistry(max_pending=2, max_payload_bytes=10, max_requester_pending=1)
    first = channel("one")
    second = channel("two")
    registry.replace(first)
    registry.replace(second)

    async def scenario():
        one = registry.new_lease(
            first,
            operation="csv_render_v1",
            payload_bytes=b"12345",
            requester_user_id=9,
            deadline_monotonic=time.monotonic() + 20,
        )
        with pytest.raises(OverflowError, match="requester_pending_limit"):
            registry.new_lease(
                second,
                operation="csv_render_v1",
                payload_bytes=b"1",
                requester_user_id=9,
                deadline_monotonic=time.monotonic() + 20,
            )
        with pytest.raises(OverflowError, match="payload_memory_limit"):
            registry.new_lease(
                second,
                operation="csv_render_v1",
                payload_bytes=b"123456",
                requester_user_id=10,
                deadline_monotonic=time.monotonic() + 20,
            )
        registry.finish(first, one)
        one.future.cancel()
        assert registry.pending_payload_bytes == 0

    asyncio.run(scenario())


def test_scheduler_respects_worker_reported_active_jobs():
    registry = ComputeRegistry()
    busy = channel("busy")
    busy.capacity = 1
    busy.reported_active_jobs = 1
    idle = channel("idle")
    registry.replace(busy)
    registry.replace(idle)

    assert registry.choose("image_resize_v1") is idle


def test_scheduler_prefers_low_pressure_measured_mac_over_hot_or_unknown_mac():
    registry = ComputeRegistry()
    hot = channel("a-hot")
    hot.cpu_percent = 65
    hot.memory_percent = 70
    unknown = channel("b-unknown")
    idle = channel("z-idle")
    idle.cpu_percent = 5
    idle.memory_percent = 10
    registry.replace(hot)
    registry.replace(unknown)
    registry.replace(idle)

    assert registry.choose("image_resize_v1") is idle


def test_scheduler_scores_reported_occupancy_before_resource_pressure():
    registry = ComputeRegistry()
    half_busy = channel("a-half-busy")
    half_busy.capacity = 2
    half_busy.reported_active_jobs = 1
    half_busy.cpu_percent = 1
    half_busy.memory_percent = 1
    free = channel("z-free")
    free.capacity = 1
    free.cpu_percent = 60
    free.memory_percent = 60
    registry.replace(half_busy)
    registry.replace(free)

    assert registry.choose("image_resize_v1") is free


def test_scheduler_uses_last_selected_as_fair_tiebreaker_for_equal_macs():
    registry = ComputeRegistry()
    first = channel("a-first")
    second = channel("b-second")
    for item in (first, second):
        item.cpu_percent = 10
        item.memory_percent = 20
        registry.replace(item)

    async def scenario():
        selected = registry.choose("image_resize_v1")
        assert selected is first
        lease = registry.new_lease(
            selected,
            operation="image_resize_v1",
            payload_bytes=b"{}",
            requester_user_id=1,
            deadline_monotonic=time.monotonic() + 20,
        )
        registry.finish(selected, lease)
        lease.future.cancel()
        assert registry.choose("image_resize_v1") is second

    asyncio.run(scenario())


def test_reconnect_cancels_old_generation_and_rejects_late_or_bad_hash_result():
    registry = ComputeRegistry()
    old = channel("mac", generation="old")
    registry.replace(old)

    async def scenario():
        lease = registry.new_lease(
            old,
            operation="csv_render_v1",
            payload_bytes=b'{"headers":["a"],"rows":[]}',
            requester_user_id=1,
            deadline_monotonic=time.monotonic() + 20,
        )
        registry.replace(channel("mac", generation="new"))
        with pytest.raises(ConnectionError, match="channel_replaced"):
            await lease.future
        assert (
            registry.resolve_result(
                device_id="mac",
                generation="old",
                job_id=lease.job_id,
                attempt_id=lease.attempt_id,
                payload_sha256=lease.payload_sha256,
                result={"ok": False, "error": "cancelled"},
            )
            is False
        )

        current = registry.channels["mac"]
        current_lease = registry.new_lease(
            current,
            operation="csv_render_v1",
            payload_bytes=b"{}",
            requester_user_id=1,
            deadline_monotonic=time.monotonic() + 20,
        )
        assert (
            registry.resolve_result(
                device_id="mac",
                generation="new",
                job_id=current_lease.job_id,
                attempt_id=current_lease.attempt_id,
                payload_sha256="0" * 64,
                result={"ok": False, "error": "invalid"},
            )
            is False
        )
        registry.finish(current, current_lease)
        current_lease.future.cancel()

    asyncio.run(scenario())


def test_remove_cancels_active_lease_and_status_is_live_and_owner_scoped():
    registry = ComputeRegistry()
    own = channel("own")
    other = channel("other")
    other.owner_user_id = 2
    registry.replace(own)
    registry.replace(other)

    async def scenario():
        lease = registry.new_lease(
            own,
            operation="image_resize_v1",
            payload_bytes=b"{}",
            requester_user_id=1,
            deadline_monotonic=time.monotonic() + 20,
        )
        registry.remove(own)
        with pytest.raises(ConnectionError, match="channel_dropped"):
            await lease.future

    asyncio.run(scenario())
    status = registry.status(owner_user_id=1)
    assert [item["id"] for item in status["devices"]] == []
    assert status["summary"]["connected"] == 0


def test_result_validation_checks_exact_bytes_hash_mime_and_dimensions():
    raw = b"hello"
    result = {
        "data_base64": base64.b64encode(raw).decode(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mime_type": "text/csv; charset=utf-8",
    }
    assert validate_result("csv_render_v1", result) == {**result, "bytes": raw}
    assert validate_result("csv_render_v1", {**result, "sha256": "0" * 64}) is None
    assert validate_result(
        "image_resize_v1",
        {**result, "mime_type": "image/jpeg", "width": 200, "height": 100},
    ) == {
        **result,
        "mime_type": "image/jpeg",
        "width": 200,
        "height": 100,
        "bytes": raw,
    }
    assert (
        validate_result(
            "image_resize_v1",
            {**result, "mime_type": "image/jpeg", "width": 5000, "height": 100},
        )
        is None
    )
