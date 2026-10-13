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
    ready: bool = False
    failure_penalty: float = 0.0
    last_heartbeat: float = field(default_factory=time.monotonic)


@dataclass
class PendingJob:
    job_id: str
    attempt_id: str
    device_id: str
    generation: str
    vin: str
    requester_user_id: int | None
    deadline_monotonic: float | None
    future: asyncio.Future


class BrokerRegistry:
    def __init__(
        self,
        *,
        max_connections: int = 100,
        max_inflight_per_device: int = 2,
        readiness_timeout_seconds: float = 30.0,
        cache_ttl_seconds: float = 3.0,
        max_cache_entries: int = 512,
        max_cache_bytes: int = 16 * 1024 * 1024,
        max_pending_jobs: int = 200,
    ) -> None:
        self.max_connections = max_connections
        self.max_inflight_per_device = max_inflight_per_device
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_cache_entries = max_cache_entries
        self.max_cache_bytes = max_cache_bytes
        self.max_pending_jobs = max_pending_jobs
        self.channels: dict[str, Channel] = {}
        self.pending: dict[tuple[str, str, str, str], PendingJob] = {}
        self.cache: dict[tuple[str, str, str], tuple[float, dict, int]] = {}
        self.cache_bytes = 0
        self.loads: dict[tuple[str, str, str], asyncio.Future] = {}
        self.request_loads: dict[tuple[int, str], asyncio.Future] = {}

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
        return old

    def remove(self, channel: Channel) -> None:
        if self.channels.get(channel.device_id) is channel:
            self.channels.pop(channel.device_id, None)
        for key, job in list(self.pending.items()):
            if job.device_id == channel.device_id and job.generation == channel.generation:
                self.pending.pop(key, None)
                if not job.future.done():
                    job.future.set_exception(ConnectionError("channel_dropped"))

    def choose(self, eligible_device_ids: set[str], *, excluded: set[str] | None = None):
        now = time.monotonic()
        excluded = excluded or set()
        candidates = [
            channel
            for device_id, channel in self.channels.items()
            if device_id in eligible_device_ids
            and device_id not in excluded
            and channel.ready
            and channel.inflight < self.max_inflight_per_device
            and now - channel.last_heartbeat <= self.readiness_timeout_seconds
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: item.latency_ms + item.inflight * 250 + item.failure_penalty * 1000,
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
            future=loop.create_future(),
        )
        self.pending[(job.device_id, job.generation, job.job_id, job.attempt_id)] = job
        channel.inflight += 1
        return job

    def finish(self, channel: Channel, job: PendingJob) -> None:
        self.pending.pop((job.device_id, job.generation, job.job_id, job.attempt_id), None)
        channel.inflight = max(0, channel.inflight - 1)

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

    def begin_request(self, requester_user_id: int, vin: str) -> tuple[asyncio.Future, bool]:
        key = (requester_user_id, vin)
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
        requester_user_id: int,
        vin: str,
        *,
        result: tuple[dict, str] | None = None,
        error: BaseException | None = None,
    ) -> None:
        future = self.request_loads.pop((requester_user_id, vin), None)
        if future is None or future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)
