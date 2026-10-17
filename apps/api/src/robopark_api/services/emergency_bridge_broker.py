from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class Channel:
    device_id: str
    owner_user_id: int
    websocket: Any
    generation: str = field(default_factory=lambda: str(uuid4()))
    latency_ms: float = 10_000.0
    inflight: int = 0
    queued: int = 0
    ready: bool = False
    failure_penalty: float = 0.0
    last_heartbeat: float = field(default_factory=time.monotonic)
    capacity: int = 2
    reported_active_jobs: int = 0
    reported_load: float = 0.0
    paused: bool = False
    power: str = "unknown"
    cpu_percent: float | None = None
    memory_percent: float | None = None
    thermal_state: str | None = None
    supports: set[str] = field(default_factory=set)
    probe_available: bool = False
    probe_inflight: bool = False
    probe_id: str | None = None
    probe_deadline: float = 0.0
    last_probe_at: float = 0.0
    last_upstream_success: float = 0.0
    last_selected: float = 0.0
    virtual_finish: float = 0.0
    requests: int = 0
    completed: int = 0
    failed: int = 0
    encoded_bytes: int = 0
    decoded_bytes: int = 0
    probes_sent: int = 0
    probes_failed: int = 0
    upstream_dispatches: int = 0
    upstream_backpressure: int = 0
    cache_hits: int = 0
    compression_saved_bytes: int = 0
    response_latency_ms: float | None = None
    response_throughput_mbps: float | None = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def send_json(self, value: dict) -> None:
        async with self.send_lock:
            await self.websocket.send_json(value)


@dataclass
class PendingJob:
    job_id: str
    attempt_id: str
    device_id: str
    generation: str
    vin: str
    requester_user_id: int | None
    deadline_monotonic: float | None
    started_at: float
    future: asyncio.Future


@dataclass(frozen=True, eq=False)
class CapacityWaiter:
    eligible_device_ids: frozenset[str]
    excluded_device_ids: frozenset[str]
    excluded_generations: frozenset[tuple[str, str]]
    deadline: float


class BrokerRegistry:
    def __init__(
        self,
        *,
        max_connections: int = 100,
        max_inflight_per_device: int = 8,
        readiness_timeout_seconds: float = 30.0,
        cache_ttl_seconds: float = 3.0,
        max_cache_entries: int = 512,
        max_cache_bytes: int = 16 * 1024 * 1024,
        max_pending_jobs: int = 200,
        upstream_reads_per_second: float = 8.0,
    ) -> None:
        self.max_connections = max_connections
        self.max_inflight_per_device = max_inflight_per_device
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_cache_entries = max_cache_entries
        self.max_cache_bytes = max_cache_bytes
        self.max_pending_jobs = max_pending_jobs
        self.upstream_reads_per_second = upstream_reads_per_second
        self.channels: dict[str, Channel] = {}
        self.pending: dict[tuple[str, str, str, str], PendingJob] = {}
        self.cache: dict[tuple[str, str, str], tuple[float, dict, int]] = {}
        self.cache_bytes = 0
        self.shared_cache: dict[tuple[tuple[int, ...], str], tuple[float, dict, str, int]] = {}
        self.shared_cache_bytes = 0
        self.loads: dict[tuple[str, str, str], asyncio.Future] = {}
        self.request_loads: dict[tuple[tuple[int, ...], str], asyncio.Future] = {}
        self.metrics = {
            "requests": 0,
            "attempts": 0,
            "completed": 0,
            "failed": 0,
            "rejected": 0,
            "encoded_bytes": 0,
            "decoded_bytes": 0,
            "probes_sent": 0,
            "probes_failed": 0,
            "upstream_dispatches": 0,
            "upstream_backpressure": 0,
            "cache_hits": 0,
            "compression_saved_bytes": 0,
        }
        self._probe_tokens = 2.0
        self._probe_refilled_at = time.monotonic()
        self._request_tokens = 256.0
        self._request_refilled_at = time.monotonic()
        self._upstream_tokens = upstream_reads_per_second
        self._upstream_refilled_at = time.monotonic()
        self._active_requests: dict[int, int] = {}
        self.max_active_requests = 256
        # A batched robot read issues one request per visible robot. Keep one
        # 60-robot batch admissible while the global bound preserves room for
        # other requesters.
        self.max_active_per_requester = 64
        self._capacity_changed = asyncio.Event()
        self._capacity_waiters: list[CapacityWaiter] = []

    def capacity_changed(self) -> None:
        previous = self._capacity_changed
        self._capacity_changed = asyncio.Event()
        previous.set()

    async def wait_for_available(
        self,
        eligible_device_ids: set[str],
        *,
        excluded_device_ids: set[str],
        excluded_generations: set[tuple[str, str]],
        deadline: float,
        wait_through_empty: bool,
    ) -> Channel | None:
        # Admission bounds waiting callers; the per-device queue remains a
        # reservation for an imminent dispatch, not another unbounded buffer.
        if len(self._capacity_waiters) >= self.max_active_requests:
            return None
        waiter = CapacityWaiter(
            eligible_device_ids=frozenset(eligible_device_ids),
            excluded_device_ids=frozenset(excluded_device_ids),
            excluded_generations=frozenset(excluded_generations),
            deadline=deadline,
        )
        self._capacity_waiters.append(waiter)
        has_seen_eligible = wait_through_empty
        try:
            while True:
                changed = self._capacity_changed
                selection_exclusions = set(waiter.excluded_device_ids)
                channel = None
                while True:
                    candidate = self.choose(
                        set(waiter.eligible_device_ids),
                        excluded=selection_exclusions,
                        excluded_generations=set(waiter.excluded_generations),
                    )
                    if candidate is None:
                        break
                    waiter_index = self._capacity_waiters.index(waiter)
                    older_waiters = self._capacity_waiters[:waiter_index]
                    if any(self._waiter_accepts(item, candidate) for item in older_waiters):
                        selection_exclusions.add(candidate.device_id)
                        continue
                    channel = candidate
                    break
                if channel is not None:
                    return channel
                live_eligible = any(
                    device_id in waiter.eligible_device_ids
                    and device_id not in waiter.excluded_device_ids
                    for device_id in self.channels
                )
                if not (waiter.eligible_device_ids - waiter.excluded_device_ids):
                    return None
                # A request already queued behind a Mac must survive its
                # reconnect, even if it has not made its first attempt yet.
                has_seen_eligible = has_seen_eligible or live_eligible
                if not has_seen_eligible:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    await asyncio.wait_for(changed.wait(), timeout=remaining)
                except TimeoutError:
                    return None
        finally:
            if waiter in self._capacity_waiters:
                self._capacity_waiters.remove(waiter)
                self.capacity_changed()

    @staticmethod
    def _waiter_accepts(waiter: CapacityWaiter, channel: Channel) -> bool:
        return (
            time.monotonic() < waiter.deadline
            and channel.device_id in waiter.eligible_device_ids
            and channel.device_id not in waiter.excluded_device_ids
            and (channel.device_id, channel.generation) not in waiter.excluded_generations
        )

    def admit_request(self, requester_user_id: int, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        elapsed = max(0.0, now - self._request_refilled_at)
        self._request_tokens = min(256.0, self._request_tokens + elapsed * 64.0)
        self._request_refilled_at = now
        active_global = sum(self._active_requests.values())
        active_owner = self._active_requests.get(requester_user_id, 0)
        if (
            self._request_tokens < 1.0
            or active_global >= self.max_active_requests
            or active_owner >= self.max_active_per_requester
        ):
            self.metrics["rejected"] += 1
            return False
        self._request_tokens -= 1.0
        self._active_requests[requester_user_id] = active_owner + 1
        self.metrics["requests"] += 1
        return True

    def release_request(self, requester_user_id: int) -> None:
        active = self._active_requests.get(requester_user_id, 0)
        if active <= 1:
            self._active_requests.pop(requester_user_id, None)
        else:
            self._active_requests[requester_user_id] = active - 1

    def try_reserve_upstream(
        self,
        channel: Channel,
        *,
        kind: str,
        now: float | None = None,
        count_backpressure: bool = True,
    ) -> bool:
        if kind not in {"job", "probe"}:
            raise ValueError("invalid_upstream_kind")
        now = time.monotonic() if now is None else now
        elapsed = max(0.0, now - self._upstream_refilled_at)
        self._upstream_tokens = min(
            self.upstream_reads_per_second,
            self._upstream_tokens + elapsed * self.upstream_reads_per_second,
        )
        self._upstream_refilled_at = now
        if self._upstream_tokens < 1.0:
            if count_backpressure:
                channel.upstream_backpressure += 1
                self.metrics["upstream_backpressure"] += 1
            return False
        self._upstream_tokens -= 1.0
        channel.upstream_dispatches += 1
        self.metrics["upstream_dispatches"] += 1
        return True

    async def wait_for_upstream(self, channel: Channel, *, deadline: float) -> bool:
        count_backpressure = True
        while True:
            now = time.monotonic()
            if self.try_reserve_upstream(
                channel,
                kind="job",
                now=now,
                count_backpressure=count_backpressure,
            ):
                return True
            count_backpressure = False
            remaining = deadline - now
            if remaining <= 0:
                return False
            await asyncio.sleep(min(remaining, 1.0 / self.upstream_reads_per_second))

    def replace(self, channel: Channel) -> Channel | None:
        old = self.channels.get(channel.device_id)
        if old is None and len(self.channels) >= self.max_connections:
            raise OverflowError("connection_limit")
        self.channels[channel.device_id] = channel
        if old is not None:
            for key, job in list(self.pending.items()):
                if job.device_id == old.device_id and job.generation == old.generation:
                    self.pending.pop(key, None)
                    if not job.future.done():
                        job.future.set_exception(ConnectionError("channel_replaced"))
        self.capacity_changed()
        return old

    def remove(self, channel: Channel) -> None:
        if self.channels.get(channel.device_id) is channel:
            self.channels.pop(channel.device_id, None)
        for key, job in list(self.pending.items()):
            if job.device_id == channel.device_id and job.generation == channel.generation:
                self.pending.pop(key, None)
                if not job.future.done():
                    job.future.set_exception(ConnectionError("channel_dropped"))
        self.capacity_changed()

    def choose(
        self,
        eligible_device_ids: set[str],
        *,
        excluded: set[str] | None = None,
        excluded_generations: set[tuple[str, str]] | None = None,
    ):
        now = time.monotonic()
        excluded = excluded or set()
        excluded_generations = excluded_generations or set()
        candidates = [
            channel
            for device_id, channel in self.channels.items()
            if device_id in eligible_device_ids
            and device_id not in excluded
            and (device_id, channel.generation) not in excluded_generations
            and channel.ready
            and not channel.paused
            and channel.thermal_state != "critical"
            and (channel.cpu_percent is None or channel.cpu_percent < 98.0)
            and (channel.memory_percent is None or channel.memory_percent < 98.0)
            and channel.inflight + channel.queued < self.effective_capacity(channel)
            and now - channel.last_heartbeat <= self.readiness_timeout_seconds
        ]
        if not candidates:
            return None

        def score(item: Channel) -> tuple[float, str]:
            capacity = max(1, item.capacity)
            observed_load = max(item.reported_load, (item.inflight + item.queued) / capacity)
            waiting = 120.0 if item.last_selected <= 0 else max(0.0, now - item.last_selected)
            aging_credit = min(waiting, 120.0) * 20.0
            latency_cost = min(item.latency_ms, 2_000.0) * 0.1
            power_cost = 200.0 if item.power in {"battery", "low_power"} else 0.0
            thermal_cost = (
                250.0
                if item.thermal_state == "serious"
                else 50.0
                if item.thermal_state == "fair"
                else 0.0
            )
            cpu_cost = max(0.0, (item.cpu_percent or 0.0) - 70.0) * 15.0
            memory_cost = max(0.0, (item.memory_percent or 0.0) - 80.0) * 10.0
            total = (
                item.virtual_finish
                + observed_load * 750.0
                + latency_cost
                + item.failure_penalty * 1_000.0
                + power_cost
                + thermal_cost
                + cpu_cost
                + memory_cost
                - aging_credit
            )
            return total, item.device_id

        return min(candidates, key=score)

    def effective_capacity(self, channel: Channel) -> int:
        if (
            channel.thermal_state == "critical"
            or (channel.cpu_percent is not None and channel.cpu_percent >= 98.0)
            or (channel.memory_percent is not None and channel.memory_percent >= 98.0)
        ):
            return 0
        capacity = min(self.max_inflight_per_device, channel.capacity)
        if channel.thermal_state == "serious":
            return min(capacity, 1)
        return capacity

    def reserve_queue_slot(self, channel: Channel) -> bool:
        if self.channels.get(channel.device_id) is not channel:
            return False
        if channel.inflight + channel.queued >= self.effective_capacity(channel):
            return False
        channel.queued += 1
        return True

    def release_queue_slot(self, channel: Channel) -> None:
        channel.queued = max(0, channel.queued - 1)
        self.capacity_changed()

    def channel_available(self, channel: Channel) -> bool:
        now = time.monotonic()
        return (
            self.channels.get(channel.device_id) is channel
            and channel.ready
            and not channel.paused
            and self.effective_capacity(channel) > channel.inflight + channel.queued
            and now - channel.last_heartbeat <= self.readiness_timeout_seconds
        )

    def new_pending(
        self,
        channel: Channel,
        vin: str,
        *,
        requester_user_id: int | None = None,
        deadline_monotonic: float | None = None,
    ) -> PendingJob:
        if len(self.pending) >= self.max_pending_jobs:
            raise OverflowError("pending_limit")
        loop = asyncio.get_running_loop()
        job = PendingJob(
            job_id=str(uuid4()),
            attempt_id=str(uuid4()),
            device_id=channel.device_id,
            generation=channel.generation,
            vin=vin,
            requester_user_id=requester_user_id,
            deadline_monotonic=deadline_monotonic,
            started_at=time.monotonic(),
            future=loop.create_future(),
        )
        self.pending[(job.device_id, job.generation, job.job_id, job.attempt_id)] = job
        channel.inflight += 1
        channel.requests += 1
        channel.last_selected = time.monotonic()
        channel.virtual_finish += 1_000.0 / max(1, channel.capacity)
        self.metrics["attempts"] += 1
        return job

    def finish(self, channel: Channel, job: PendingJob) -> None:
        self.pending.pop((job.device_id, job.generation, job.job_id, job.attempt_id), None)
        channel.inflight = max(0, channel.inflight - 1)
        self.capacity_changed()

    def record_success(
        self,
        channel: Channel,
        *,
        encoded_bytes: int,
        decoded_bytes: int,
        duration_seconds: float | None = None,
        now: float | None = None,
    ) -> None:
        now = time.monotonic() if now is None else now
        channel.completed += 1
        channel.encoded_bytes += encoded_bytes
        channel.decoded_bytes += decoded_bytes
        channel.compression_saved_bytes += max(0, decoded_bytes - encoded_bytes)
        self.metrics["compression_saved_bytes"] += max(0, decoded_bytes - encoded_bytes)
        if duration_seconds is not None and 0.001 <= duration_seconds <= 120.0:
            latency_ms = duration_seconds * 1_000.0
            throughput_mbps = min(10_000.0, encoded_bytes * 8.0 / duration_seconds / 1_000_000.0)
            if channel.response_latency_ms is None:
                channel.response_latency_ms = latency_ms
                channel.response_throughput_mbps = throughput_mbps
            else:
                channel.response_latency_ms = channel.response_latency_ms * 0.7 + latency_ms * 0.3
                prior = channel.response_throughput_mbps or 0.0
                channel.response_throughput_mbps = prior * 0.7 + throughput_mbps * 0.3
        channel.last_upstream_success = now
        channel.last_probe_at = now
        channel.probe_inflight = False
        self.metrics["completed"] += 1
        self.metrics["encoded_bytes"] += encoded_bytes
        self.metrics["decoded_bytes"] += decoded_bytes

    def record_failure(self, channel: Channel) -> None:
        channel.failed += 1
        self.metrics["failed"] += 1

    def probe_due(self, channel: Channel, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if channel.probe_inflight and now > channel.probe_deadline:
            channel.probe_inflight = False
            channel.probe_id = None
            channel.probes_failed += 1
            self.metrics["probes_failed"] += 1
        return (
            "managed_probes" in channel.supports
            and channel.probe_available
            and not channel.paused
            and not channel.probe_inflight
            and now - max(channel.last_probe_at, channel.last_upstream_success) >= 60.0
        )

    def reserve_probe(self, channel: Channel, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if not self.probe_due(channel, now=now):
            return False
        elapsed = max(0.0, now - self._probe_refilled_at)
        self._probe_tokens = min(2.0, self._probe_tokens + elapsed * 2.0)
        self._probe_refilled_at = now
        if self._probe_tokens < 1.0:
            return False
        if not self.try_reserve_upstream(channel, kind="probe", now=now):
            return False
        self._probe_tokens -= 1.0
        channel.probe_inflight = True
        channel.probe_id = str(uuid4())
        channel.probe_deadline = now + 10.0
        channel.last_probe_at = now
        channel.probes_sent += 1
        self.metrics["probes_sent"] += 1
        return True

    def due_probe_channels(self, *, now: float | None = None) -> list[Channel]:
        now = time.monotonic() if now is None else now
        due = [channel for channel in self.channels.values() if self.probe_due(channel, now=now)]
        return sorted(
            due,
            key=lambda channel: (
                max(channel.last_probe_at, channel.last_upstream_success),
                channel.device_id,
            ),
        )

    def finish_probe(
        self,
        channel: Channel,
        *,
        probe_id: str,
        ready: bool,
        latency_ms: float | None,
        now: float | None = None,
    ) -> bool:
        now = time.monotonic() if now is None else now
        if (
            not channel.probe_inflight
            or channel.probe_id != probe_id
            or now > channel.probe_deadline
        ):
            return False
        channel.probe_inflight = False
        channel.probe_id = None
        channel.ready = ready
        channel.last_heartbeat = now
        if latency_ms is not None and 0 <= latency_ms <= 120_000:
            channel.latency_ms = latency_ms
        if not ready:
            channel.probes_failed += 1
            self.metrics["probes_failed"] += 1
        self.capacity_changed()
        return True

    def status(self, *, owner_user_id: int | None = None) -> dict:
        now = time.monotonic()
        channels = [
            item
            for item in self.channels.values()
            if owner_user_id is None or item.owner_user_id == owner_user_id
        ]
        devices = []
        for item in sorted(channels, key=lambda value: value.device_id):
            stale = now - item.last_heartbeat > self.readiness_timeout_seconds
            response_measurement_fresh = (
                not stale
                and item.last_upstream_success > 0
                and now - item.last_upstream_success <= 60.0
            )
            busy = item.inflight + item.queued >= self.effective_capacity(item)
            status = (
                "offline"
                if stale
                else "paused"
                if item.paused
                else "busy"
                if item.ready and busy
                else "ready"
                if item.ready
                else "corporate_unavailable"
                if "managed_probes" in item.supports and not item.probe_available
                else "online"
            )
            devices.append(
                {
                    "id": item.device_id,
                    "status": status,
                    "connected": True,
                    "ready": bool(item.ready and not stale and not item.paused and not busy),
                    "fresh": not stale,
                    "busy": busy,
                    "capacity": item.capacity,
                    "inflight": item.inflight,
                    "queued": item.queued,
                    "load": item.reported_load if not stale else 0.0,
                    "latency_ms": item.latency_ms if not stale else None,
                    "response_latency_ms": (
                        item.response_latency_ms if response_measurement_fresh else None
                    ),
                    "throughput_mbps": (
                        item.response_throughput_mbps if response_measurement_fresh else None
                    ),
                    "last_success_age_seconds": (
                        max(0.0, now - item.last_upstream_success)
                        if not stale and item.last_upstream_success > 0
                        else None
                    ),
                    "power": item.power,
                    "cpu_percent": item.cpu_percent if not stale else None,
                    "memory_percent": item.memory_percent if not stale else None,
                    "thermal_state": item.thermal_state if not stale else None,
                    "requests": item.requests,
                    "completed": item.completed,
                    "failed": item.failed,
                    "encoded_bytes": item.encoded_bytes,
                    "decoded_bytes": item.decoded_bytes,
                    "probes_sent": item.probes_sent,
                    "probes_failed": item.probes_failed,
                    "upstream_dispatches": item.upstream_dispatches,
                    "upstream_backpressure": item.upstream_backpressure,
                    "cache_hits": item.cache_hits,
                    "compression_saved_bytes": item.compression_saved_bytes,
                }
            )
        return {"devices": devices}

    def resolve_result(
        self,
        *,
        device_id: str,
        generation: str,
        job_id: str,
        attempt_id: str,
        result: dict,
    ) -> bool:
        key = (device_id, generation, job_id, attempt_id)
        job = self.pending.get(key)
        channel = self.channels.get(device_id)
        if job is None or channel is None or channel.generation != generation:
            return False
        if not job.future.done():
            job.future.set_result(result)
        return True

    def pending_job(
        self, *, device_id: str, generation: str, job_id: str, attempt_id: str
    ) -> PendingJob | None:
        return self.pending.get((device_id, generation, job_id, attempt_id))

    def cache_get(self, channel: Channel, vin: str) -> dict | None:
        key = (channel.device_id, channel.generation, vin)
        cached = self.cache.get(key)
        if cached is None:
            return None
        loaded_at, payload, size_bytes = cached
        if time.monotonic() - loaded_at > self.cache_ttl_seconds:
            self.cache.pop(key, None)
            self.cache_bytes = max(0, self.cache_bytes - size_bytes)
            return None
        return payload

    def cache_put(self, channel: Channel, vin: str, payload: dict) -> None:
        key = (channel.device_id, channel.generation, vin)
        previous = self.cache.pop(key, None)
        if previous is not None:
            self.cache_bytes = max(0, self.cache_bytes - previous[2])
        size_bytes = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
        while self.cache and (
            len(self.cache) >= self.max_cache_entries
            or self.cache_bytes + size_bytes > self.max_cache_bytes
        ):
            oldest = min(self.cache, key=lambda key: self.cache[key][0])
            removed = self.cache.pop(oldest)
            self.cache_bytes = max(0, self.cache_bytes - removed[2])
        if size_bytes <= self.max_cache_bytes:
            self.cache[key] = (time.monotonic(), payload, size_bytes)
            self.cache_bytes += size_bytes

    def shared_cache_get(self, owner_scope: tuple[int, ...], vin: str) -> tuple[dict, str] | None:
        key = (owner_scope, vin)
        cached = self.shared_cache.get(key)
        if cached is None:
            return None
        loaded_at, payload, source_device_id, size_bytes = cached
        if time.monotonic() - loaded_at > self.cache_ttl_seconds:
            self.shared_cache.pop(key, None)
            self.shared_cache_bytes = max(0, self.shared_cache_bytes - size_bytes)
            return None
        channel = self.channels.get(source_device_id)
        if channel is not None:
            channel.cache_hits += 1
        self.metrics["cache_hits"] += 1
        return payload, source_device_id

    def shared_cache_put(
        self,
        owner_scope: tuple[int, ...],
        vin: str,
        payload: dict,
        *,
        source_device_id: str,
    ) -> None:
        key = (owner_scope, vin)
        previous = self.shared_cache.pop(key, None)
        if previous is not None:
            self.shared_cache_bytes = max(0, self.shared_cache_bytes - previous[3])
        size_bytes = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
        while self.shared_cache and (
            len(self.shared_cache) >= self.max_cache_entries
            or self.shared_cache_bytes + size_bytes > self.max_cache_bytes
        ):
            oldest = min(self.shared_cache, key=lambda key: self.shared_cache[key][0])
            removed = self.shared_cache.pop(oldest)
            self.shared_cache_bytes = max(0, self.shared_cache_bytes - removed[3])
        if size_bytes <= self.max_cache_bytes:
            self.shared_cache[key] = (
                time.monotonic(),
                payload,
                source_device_id,
                size_bytes,
            )
            self.shared_cache_bytes += size_bytes

    def shared_cache_remove(self, owner_scope: tuple[int, ...], vin: str) -> None:
        cached = self.shared_cache.pop((owner_scope, vin), None)
        if cached is not None:
            self.shared_cache_bytes = max(0, self.shared_cache_bytes - cached[3])

    def begin_load(self, channel: Channel, vin: str) -> tuple[asyncio.Future, bool]:
        key = (channel.device_id, channel.generation, vin)
        existing = self.loads.get(key)
        if existing is not None:
            return existing, False
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(
            lambda completed: completed.exception() if not completed.cancelled() else None
        )
        self.loads[key] = future
        return future, True

    def finish_load(
        self,
        channel: Channel,
        vin: str,
        *,
        payload: dict | None = None,
        error: BaseException | None = None,
    ) -> None:
        key = (channel.device_id, channel.generation, vin)
        future = self.loads.pop(key, None)
        if future is None or future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(payload)

    def begin_request(self, owner_scope: tuple[int, ...], vin: str) -> tuple[asyncio.Future, bool]:
        key = (owner_scope, vin)
        existing = self.request_loads.get(key)
        if existing is not None:
            return existing, False
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(
            lambda completed: completed.exception() if not completed.cancelled() else None
        )
        self.request_loads[key] = future
        return future, True

    def finish_request(
        self,
        owner_scope: tuple[int, ...],
        vin: str,
        *,
        result: tuple[dict, str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        future = self.request_loads.pop((owner_scope, vin), None)
        if future is None or future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)
