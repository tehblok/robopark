import asyncio
import base64
import hashlib
import time

import pytest

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
