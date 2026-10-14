from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

OPERATIONS = frozenset({"image_resize_v1", "csv_render_v1"})
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024


@dataclass
class ComputeChannel:
    device_id: str
    owner_user_id: int
    websocket: Any
    generation: str = field(default_factory=lambda: str(uuid4()))
    ready: bool = False
    paused: bool = False
    capacity: int = 1
    inflight: int = 0
    reported_active_jobs: int = 0
    cpu_percent: float | None = None
    memory_percent: float | None = None
    thermal_state: str = "normal"
    power: str = "unknown"
    supports: set[str] = field(default_factory=set)
    version: str | None = None
    last_heartbeat: float = field(default_factory=time.monotonic)
    last_selected: float = 0.0
    completed: int = 0
    failed: int = 0
    direct_input_bytes: int = 0
    direct_output_bytes: int = 0
    signaling_bytes: int = 0
    direct_completed: int = 0
    direct_failed: int = 0
    direct_cancelled: int = 0
    direct_retries: int = 0
    direct_fallback: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def send_json(self, value: dict) -> None:
        async with self.send_lock:
            await self.websocket.send_json(value)


@dataclass
class ComputeLease:
    job_id: str
    attempt_id: str
    device_id: str
    generation: str
    operation: str
    payload_sha256: str
    payload_size: int
    requester_user_id: int
    deadline_monotonic: float
    started_at: float
    future: asyncio.Future
    direct: bool = False
    token: str | None = None
    direct_payload_size: int = 0


class ComputeRegistry:
    def __init__(
        self,
        *,
        max_connections: int = 100,
        max_pending: int = 12,
        max_requester_pending: int = 2,
        max_payload_bytes: int = 48 * 1024 * 1024,
        readiness_timeout_seconds: float = 30.0,
    ) -> None:
        self.max_connections = max_connections
        self.max_pending = max_pending
        self.max_requester_pending = max_requester_pending
        self.max_payload_bytes = max_payload_bytes
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.channels: dict[str, ComputeChannel] = {}
        self.pending: dict[tuple[str, str, str, str], ComputeLease] = {}
        self.pending_payload_bytes = 0
        self.metrics = {
            "direct_input_bytes": 0,
            "direct_output_bytes": 0,
            "signaling_bytes": 0,
            "direct_completed": 0,
            "direct_failed": 0,
            "direct_cancelled": 0,
            "direct_retries": 0,
            "direct_fallback": 0,
        }

    def replace(self, channel: ComputeChannel) -> ComputeChannel | None:
        old = self.channels.get(channel.device_id)
        if old is None and len(self.channels) >= self.max_connections:
            raise OverflowError("connection_limit")
        self.channels[channel.device_id] = channel
        if old is not None:
            self._fail_channel(old, "channel_replaced")
        return old

    def remove(self, channel: ComputeChannel) -> None:
        if self.channels.get(channel.device_id) is channel:
            self.channels.pop(channel.device_id, None)
        self._fail_channel(channel, "channel_dropped")

    def _fail_channel(self, channel: ComputeChannel, reason: str) -> None:
        for lease in list(self.pending.values()):
            if lease.device_id == channel.device_id and lease.generation == channel.generation:
                self.finish(channel, lease)
                if not lease.future.done():
                    lease.future.set_exception(ConnectionError(reason))

    def effective_capacity(self, channel: ComputeChannel) -> int:
        if (
            channel.paused
            or channel.thermal_state in {"serious", "critical"}
            or (channel.cpu_percent is not None and channel.cpu_percent >= 70)
            or (channel.memory_percent is not None and channel.memory_percent >= 80)
            or channel.power == "low_power"
        ):
            return 0
        return min(2, max(0, channel.capacity))

    def choose(
        self,
        operation: str,
        *,
        excluded: set[str] | None = None,
        required_supports: set[str] | None = None,
    ) -> ComputeChannel | None:
        now = time.monotonic()
        excluded = excluded or set()
        candidates = [
            item
            for item in self.channels.values()
            if item.device_id not in excluded
            and item.ready
            and operation in item.supports
            and (required_supports or set()).issubset(item.supports)
            and now - item.last_heartbeat <= self.readiness_timeout_seconds
            and max(item.inflight, item.reported_active_jobs) < self.effective_capacity(item)
        ]
        if not candidates:
            return None

        def score(item: ComputeChannel) -> tuple[float, float, float, str]:
            capacity = self.effective_capacity(item)
            occupancy = max(item.inflight, item.reported_active_jobs) / capacity
            cpu_pressure = 0.75 if item.cpu_percent is None else min(1.0, item.cpu_percent / 70)
            memory_pressure = (
                0.75 if item.memory_percent is None else min(1.0, item.memory_percent / 80)
            )
            resource_pressure = max(cpu_pressure, memory_pressure)
            return occupancy, resource_pressure, item.last_selected, item.device_id

        return min(candidates, key=score)

    def new_lease(
        self,
        channel: ComputeChannel,
        *,
        operation: str,
        payload_bytes: bytes,
        requester_user_id: int,
        deadline_monotonic: float,
    ) -> ComputeLease:
        if operation not in OPERATIONS:
            raise ValueError("unsupported_operation")
        if len(self.pending) >= self.max_pending:
            raise OverflowError("pending_limit")
        requester_pending = sum(
            item.requester_user_id == requester_user_id for item in self.pending.values()
        )
        if requester_pending >= self.max_requester_pending:
            raise OverflowError("requester_pending_limit")
        if self.pending_payload_bytes + len(payload_bytes) > self.max_payload_bytes:
            raise OverflowError("payload_memory_limit")
        lease = ComputeLease(
            job_id=str(uuid4()),
            attempt_id=str(uuid4()),
            device_id=channel.device_id,
            generation=channel.generation,
            operation=operation,
            payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
            payload_size=len(payload_bytes),
            requester_user_id=requester_user_id,
            deadline_monotonic=deadline_monotonic,
            started_at=time.monotonic(),
            future=asyncio.get_running_loop().create_future(),
        )
        self.pending[(lease.device_id, lease.generation, lease.job_id, lease.attempt_id)] = lease
        self.pending_payload_bytes += lease.payload_size
        channel.inflight += 1
        channel.last_selected = time.monotonic()
        return lease

    def new_direct_lease(
        self,
        channel: ComputeChannel,
        *,
        operation: str,
        payload_sha256: str,
        payload_size: int,
        requester_user_id: int,
        deadline_monotonic: float,
        token: str,
    ) -> ComputeLease:
        if operation != "image_resize_v1" or len(payload_sha256) != 64:
            raise ValueError("invalid_direct_operation")
        if len(self.pending) >= self.max_pending:
            raise OverflowError("pending_limit")
        requester_pending = sum(
            item.requester_user_id == requester_user_id for item in self.pending.values()
        )
        if requester_pending >= self.max_requester_pending:
            raise OverflowError("requester_pending_limit")
        lease = ComputeLease(
            job_id=str(uuid4()),
            attempt_id=str(uuid4()),
            device_id=channel.device_id,
            generation=channel.generation,
            operation=operation,
            payload_sha256=payload_sha256,
            payload_size=0,
            requester_user_id=requester_user_id,
            deadline_monotonic=deadline_monotonic,
            started_at=time.monotonic(),
            future=asyncio.get_running_loop().create_future(),
            direct=True,
            token=token,
            direct_payload_size=payload_size,
        )
        self.pending[(lease.device_id, lease.generation, lease.job_id, lease.attempt_id)] = lease
        channel.inflight += 1
        channel.last_selected = time.monotonic()
        return lease

    def finish(self, channel: ComputeChannel, lease: ComputeLease) -> None:
        key = (lease.device_id, lease.generation, lease.job_id, lease.attempt_id)
        if self.pending.pop(key, None) is not None:
            self.pending_payload_bytes = max(0, self.pending_payload_bytes - lease.payload_size)
            channel.inflight = max(0, channel.inflight - 1)

    def pending_lease(
        self, *, device_id: str, generation: str, job_id: str, attempt_id: str
    ) -> ComputeLease | None:
        return self.pending.get((device_id, generation, job_id, attempt_id))

    def resolve_result(
        self,
        *,
        device_id: str,
        generation: str,
        job_id: str,
        attempt_id: str,
        payload_sha256: str,
        result: dict,
    ) -> bool:
        lease = self.pending_lease(
            device_id=device_id,
            generation=generation,
            job_id=job_id,
            attempt_id=attempt_id,
        )
        if lease is None or lease.payload_sha256 != payload_sha256 or lease.future.done():
            return False
        lease.future.set_result(result)
        return True

    def status(self, *, owner_user_id: int | None = None) -> dict:
        now = time.monotonic()
        devices = []
        for item in sorted(self.channels.values(), key=lambda channel: channel.device_id):
            if owner_user_id is not None and item.owner_user_id != owner_user_id:
                continue
            fresh = now - item.last_heartbeat <= self.readiness_timeout_seconds
            capacity = self.effective_capacity(item) if fresh else 0
            active_jobs = max(item.inflight, item.reported_active_jobs)
            ready = bool(fresh and item.ready and active_jobs < capacity)
            status = (
                "offline"
                if not fresh
                else "paused"
                if item.paused or capacity == 0
                else "busy"
                if active_jobs >= capacity
                else "ready"
                if ready
                else "online"
            )
            devices.append(
                {
                    "id": item.device_id,
                    "status": status,
                    "connected": True,
                    "ready": ready,
                    "capacity": capacity,
                    "inflight": item.inflight,
                    "active_jobs": active_jobs,
                    "cpu_percent": item.cpu_percent if fresh else None,
                    "memory_percent": item.memory_percent if fresh else None,
                    "thermal_state": item.thermal_state if fresh else None,
                    "power": item.power,
                    "supports": sorted(item.supports),
                    "version": item.version,
                    "completed": item.completed,
                    "failed": item.failed,
                    "direct_input_bytes": item.direct_input_bytes,
                    "direct_output_bytes": item.direct_output_bytes,
                    "signaling_bytes": item.signaling_bytes,
                    "direct_completed": item.direct_completed,
                    "direct_failed": item.direct_failed,
                    "direct_cancelled": item.direct_cancelled,
                    "direct_retries": item.direct_retries,
                    "direct_fallback": item.direct_fallback,
                }
            )
        return {
            "summary": {
                "connected": len(devices),
                "ready": sum(item["ready"] for item in devices),
                "capacity": sum(item["capacity"] for item in devices),
                "inflight": sum(item["inflight"] for item in devices),
                "active_jobs": sum(item["active_jobs"] for item in devices),
                "completed": sum(item["completed"] for item in devices),
                "failed": sum(item["failed"] for item in devices),
                "direct_input_bytes": sum(item["direct_input_bytes"] for item in devices),
                "direct_output_bytes": sum(item["direct_output_bytes"] for item in devices),
                "signaling_bytes": sum(item["signaling_bytes"] for item in devices),
                "direct_completed": sum(item["direct_completed"] for item in devices),
                "direct_failed": sum(item["direct_failed"] for item in devices),
                "direct_cancelled": sum(item["direct_cancelled"] for item in devices),
                "direct_retries": sum(item["direct_retries"] for item in devices),
                "direct_fallback": sum(item["direct_fallback"] for item in devices),
            },
            "devices": devices,
        }


def validate_result(operation: str, result: object) -> dict | None:
    if operation not in OPERATIONS or not isinstance(result, dict):
        return None
    data_base64 = result.get("data_base64")
    digest = result.get("sha256")
    mime_type = result.get("mime_type")
    if not isinstance(data_base64, str) or not isinstance(digest, str):
        return None
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except (TypeError, ValueError):
        return None
    limit = MAX_IMAGE_BYTES if operation == "image_resize_v1" else MAX_RESULT_BYTES
    if not raw or len(raw) > limit or hashlib.sha256(raw).hexdigest() != digest:
        return None
    if operation == "csv_render_v1":
        if mime_type != "text/csv; charset=utf-8":
            return None
    else:
        width = result.get("width")
        height = result.get("height")
        if (
            mime_type != "image/jpeg"
            or type(width) is not int
            or type(height) is not int
            or not 1 <= width <= 4096
            or not 1 <= height <= 4096
            or width * height > 40_000_000
        ):
            return None
    return {**result, "bytes": raw}
