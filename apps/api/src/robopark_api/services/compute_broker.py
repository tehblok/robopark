from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from robopark_api.services import corporate_gateway_contract, corporate_write_contract
from robopark_api.services.compute_routing_health import RoutingHealth

LOCAL_OPERATIONS = frozenset({"image_resize_v1", "csv_render_v1"})
OPERATIONS = LOCAL_OPERATIONS | {
    corporate_gateway_contract.OPERATION,
    corporate_write_contract.OPERATION,
}
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
RELAY_TICKET_TTL_SECONDS = 30.0
RELAY_CLAIM_TTL_SECONDS = 60.0
RELAY_BUDGET_BYTES_PER_MINUTE = 64 * 1024 * 1024


@dataclass
class RelayTicket:
    token: str
    requester_user_id: int
    payload_bytes: int
    payload_sha256: str
    reserved_bytes: int
    created_at: float
    expires_at: float
    claimed: bool = False


class RelayAdmissionPool:
    """Small in-memory gate placed before any relay payload is uploaded."""

    def __init__(
        self,
        *,
        max_active: int = 2,
        max_requester_active: int = 1,
        budget_bytes_per_minute: int = RELAY_BUDGET_BYTES_PER_MINUTE,
        ticket_ttl_seconds: float = RELAY_TICKET_TTL_SECONDS,
        claim_ttl_seconds: float = RELAY_CLAIM_TTL_SECONDS,
        now: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.max_active = max_active
        self.max_requester_active = max_requester_active
        self.budget_bytes_per_minute = budget_bytes_per_minute
        self.ticket_ttl_seconds = min(30.0, ticket_ttl_seconds)
        self.claim_ttl_seconds = max(
            self.ticket_ttl_seconds,
            min(RELAY_CLAIM_TTL_SECONDS, claim_ttl_seconds),
        )
        self._now = now
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._tickets: dict[str, RelayTicket] = {}
        self._expired: dict[str, float] = {}
        self._budget_events: deque[tuple[float, str, int]] = deque()
        self._metrics = {"relay_admitted": 0, "relay_rejected": 0, "relay_payload_bytes": 0}

    @staticmethod
    def estimated_transport_bytes(payload_bytes: int) -> int:
        # Covers browser→API, internal base64 envelopes, up to two worker attempts,
        # bounded JPEG result envelopes, and a small fixed signaling allowance.
        return payload_bytes * 10 + 64 * 1024

    def _prune(self) -> None:
        now = self._now()
        for token, ticket in list(self._tickets.items()):
            if ticket.expires_at <= now:
                self._tickets.pop(token, None)
                self._expired[token] = now + 60.0
                if not ticket.claimed:
                    self._remove_budget_event(token)
        while self._budget_events and self._budget_events[0][0] <= now - 60.0:
            self._budget_events.popleft()
        for token, expires_at in list(self._expired.items()):
            if expires_at <= now:
                self._expired.pop(token, None)

    def _remove_budget_event(self, token: str) -> None:
        self._budget_events = deque(item for item in self._budget_events if item[1] != token)

    def _reject(self, reason: str) -> None:
        self._metrics["relay_rejected"] += 1
        raise OverflowError(reason)

    def admit(
        self,
        *,
        requester_user_id: int,
        payload_bytes: int,
        payload_sha256: str,
        worker_available: bool,
    ) -> RelayTicket:
        self._prune()
        if not worker_available:
            self._reject("relay_unavailable")
        if len(self._tickets) >= self.max_active:
            self._reject("relay_busy")
        if (
            sum(ticket.requester_user_id == requester_user_id for ticket in self._tickets.values())
            >= self.max_requester_active
        ):
            self._reject("relay_requester_busy")
        reserved_bytes = self.estimated_transport_bytes(payload_bytes)
        if (
            sum(item[2] for item in self._budget_events) + reserved_bytes
            > self.budget_bytes_per_minute
        ):
            self._reject("relay_budget_exhausted")
        token = self._token_factory()
        if token in self._tickets:
            raise RuntimeError("relay_ticket_collision")
        now = self._now()
        ticket = RelayTicket(
            token=token,
            requester_user_id=requester_user_id,
            payload_bytes=payload_bytes,
            payload_sha256=payload_sha256,
            reserved_bytes=reserved_bytes,
            created_at=now,
            expires_at=now + self.ticket_ttl_seconds,
        )
        self._tickets[token] = ticket
        self._budget_events.append((now, token, reserved_bytes))
        self._metrics["relay_admitted"] += 1
        return ticket

    def claim(self, token: str, *, requester_user_id: int, payload_bytes: int) -> RelayTicket:
        self._prune()
        ticket = self._tickets.get(token)
        if ticket is None:
            reason = "relay_ticket_expired" if token in self._expired else "relay_ticket_not_found"
            raise LookupError(reason)
        if ticket.requester_user_id != requester_user_id:
            raise PermissionError("relay_ticket_foreign")
        if ticket.claimed:
            raise LookupError("relay_ticket_consumed")
        if ticket.payload_bytes != payload_bytes:
            raise ValueError("relay_payload_size_mismatch")
        ticket.claimed = True
        ticket.expires_at = self._now() + self.claim_ttl_seconds
        return ticket

    def complete(self, token: str, *, requester_user_id: int, payload_bytes: int) -> None:
        self._prune()
        ticket = self._tickets.get(token)
        if ticket is None:
            raise LookupError("relay_ticket_not_found")
        if ticket.requester_user_id != requester_user_id:
            raise PermissionError("relay_ticket_foreign")
        if not ticket.claimed:
            raise LookupError("relay_ticket_not_claimed")
        self._tickets.pop(token, None)
        self._metrics["relay_payload_bytes"] += payload_bytes

    def authorize_dispatch(
        self,
        token: str,
        *,
        requester_user_id: int,
        payload_bytes: int,
        payload_sha256: str,
    ) -> RelayTicket:
        ticket = self.ticket(token, requester_user_id=requester_user_id)
        if not ticket.claimed:
            raise LookupError("relay_ticket_not_claimed")
        if ticket.payload_bytes != payload_bytes:
            raise ValueError("relay_payload_size_mismatch")
        if ticket.payload_sha256 != payload_sha256:
            raise ValueError("relay_payload_hash_mismatch")
        return ticket

    def cancel(self, token: str, *, requester_user_id: int) -> bool:
        self._prune()
        ticket = self._tickets.get(token)
        if ticket is None:
            return False
        if ticket.requester_user_id != requester_user_id:
            raise PermissionError("relay_ticket_foreign")
        if ticket.claimed:
            return False
        self._tickets.pop(token, None)
        self._remove_budget_event(token)
        return True

    def abort(self, token: str, *, requester_user_id: int) -> bool:
        """Release a claimed ticket after the trusted API stopped before dispatch."""
        self._prune()
        ticket = self._tickets.get(token)
        if ticket is None:
            return False
        if ticket.requester_user_id != requester_user_id:
            raise PermissionError("relay_ticket_foreign")
        self._tickets.pop(token, None)
        if not ticket.claimed:
            self._remove_budget_event(token)
        return True

    def ticket(self, token: str, *, requester_user_id: int) -> RelayTicket:
        self._prune()
        ticket = self._tickets.get(token)
        if ticket is None:
            raise LookupError("relay_ticket_not_found")
        if ticket.requester_user_id != requester_user_id:
            raise PermissionError("relay_ticket_foreign")
        return ticket

    def status(self) -> dict[str, int]:
        self._prune()
        reserved = sum(item[2] for item in self._budget_events)
        return {
            **self._metrics,
            "relay_active": len(self._tickets),
            "relay_budget_limit_bytes": self.budget_bytes_per_minute,
            "relay_budget_reserved_bytes": reserved,
            "relay_budget_remaining_bytes": max(0, self.budget_bytes_per_minute - reserved),
        }


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
    corporate_read_capacity: int = 0
    corporate_write_capacity: int = 0
    reported_corporate_active_jobs: int = 0
    reported_corporate_active_writes: int = 0
    corporate_inventory_status: str = "unverified_host_acl"
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
    routing_key: str = ""
    health_recorded: bool = False
    direct: bool = False
    token: str | None = None
    direct_payload_size: int = 0


@dataclass(frozen=True)
class CorporateReadWaiter:
    requester_user_id: int
    required_supports: frozenset[str]
    eligible_owner_ids: frozenset[int]
    excluded_device_ids: frozenset[str]
    excluded_generations: frozenset[tuple[str, str]]
    routing_key: str
    deadline: float


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
        self.max_corporate_pending = 128
        self.max_corporate_requester_pending = 16
        self.max_corporate_payload_bytes = 4 * 1024 * 1024
        self.corporate_pending_payload_bytes = 0
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.channels: dict[str, ComputeChannel] = {}
        self.pending: dict[tuple[str, str, str, str], ComputeLease] = {}
        self.pending_payload_bytes = 0
        self.relay_admission = RelayAdmissionPool()
        self.routing_health = RoutingHealth()
        self._capacity_changed = asyncio.Event()
        self._read_waiters: list[CorporateReadWaiter] = []
        self._read_reservations: dict[tuple[str, str, int], int] = {}
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

    def capacity_changed(self) -> None:
        previous = self._capacity_changed
        self._capacity_changed = asyncio.Event()
        previous.set()

    async def wait_for_read_slot(
        self,
        *,
        requester_user_id: int,
        deadline: float,
        required_supports: set[str],
        eligible_owner_ids: set[int] | frozenset[int],
        routing_key: str,
        excluded: set[str] | None = None,
        excluded_generations: set[tuple[str, str]] | None = None,
        wait_through_empty: bool = False,
    ) -> ComputeChannel | None:
        waiter = CorporateReadWaiter(
            requester_user_id=requester_user_id,
            required_supports=frozenset(required_supports),
            eligible_owner_ids=frozenset(eligible_owner_ids),
            excluded_device_ids=frozenset(excluded or set()),
            excluded_generations=frozenset(excluded_generations or set()),
            routing_key=routing_key,
            deadline=deadline,
        )
        self._read_waiters.append(waiter)
        has_seen_eligible = wait_through_empty
        try:
            while True:
                changed = self._capacity_changed
                selection_exclusions = set(waiter.excluded_device_ids)
                selection_exclusions.update(
                    device_id
                    for device_id, generation in waiter.excluded_generations
                    if self.channels.get(device_id) is not None
                    and self.channels[device_id].generation == generation
                )
                channel = None
                while True:
                    candidate = self.choose(
                        corporate_gateway_contract.OPERATION,
                        excluded=selection_exclusions,
                        required_supports=set(waiter.required_supports),
                        eligible_owner_ids=waiter.eligible_owner_ids,
                        routing_key=waiter.routing_key,
                    )
                    if candidate is None:
                        break
                    waiter_index = self._read_waiters.index(waiter)
                    if any(
                        self._read_waiter_accepts(older, candidate)
                        for older in self._read_waiters[:waiter_index]
                    ):
                        selection_exclusions.add(candidate.device_id)
                        continue
                    channel = candidate
                    break
                corporate = [
                    item
                    for item in self.pending.values()
                    if item.operation == corporate_gateway_contract.OPERATION
                ]
                reserved = sum(self._read_reservations.values())
                requester_pending = sum(
                    item.requester_user_id == requester_user_id for item in corporate
                ) + sum(
                    count
                    for (_, _, reserved_requester), count in self._read_reservations.items()
                    if reserved_requester == requester_user_id
                )
                if (
                    channel is not None
                    and len(corporate) + reserved < self.max_corporate_pending
                    and requester_pending < self.max_corporate_requester_pending
                    and self.corporate_pending_payload_bytes
                    + (reserved + 1) * corporate_gateway_contract.MAX_REQUEST_BYTES
                    <= self.max_corporate_payload_bytes
                ):
                    key = (channel.device_id, channel.generation, requester_user_id)
                    self._read_reservations[key] = self._read_reservations.get(key, 0) + 1
                    return channel
                live_eligible = any(
                    channel.owner_user_id in waiter.eligible_owner_ids
                    and waiter.required_supports.issubset(channel.supports)
                    and channel.device_id not in waiter.excluded_device_ids
                    and (channel.device_id, channel.generation) not in waiter.excluded_generations
                    for channel in self.channels.values()
                )
                has_seen_eligible = has_seen_eligible or live_eligible
                if not has_seen_eligible:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                # A timed wake covers routing-health cooldown expiry.
                try:
                    await asyncio.wait_for(changed.wait(), timeout=min(remaining, 1.0))
                except TimeoutError:
                    continue
        finally:
            with suppress(ValueError):
                self._read_waiters.remove(waiter)
            self.capacity_changed()

    def _read_waiter_accepts(self, waiter: CorporateReadWaiter, channel: ComputeChannel) -> bool:
        requester_pending = sum(
            lease.operation == corporate_gateway_contract.OPERATION
            and lease.requester_user_id == waiter.requester_user_id
            for lease in self.pending.values()
        ) + sum(
            count
            for (_, _, requester_user_id), count in self._read_reservations.items()
            if requester_user_id == waiter.requester_user_id
        )
        return (
            time.monotonic() < waiter.deadline
            and requester_pending < self.max_corporate_requester_pending
            and channel.owner_user_id in waiter.eligible_owner_ids
            and channel.ready
            and waiter.required_supports.issubset(channel.supports)
            and (
                "corporate_inventory_read_v1" not in waiter.required_supports
                or channel.corporate_inventory_status in {"ready", "unverified_host_acl"}
            )
            and self.routing_health.available(channel.device_id, waiter.routing_key)
            and time.monotonic() - channel.last_heartbeat <= self.readiness_timeout_seconds
            and channel.device_id not in waiter.excluded_device_ids
            and (channel.device_id, channel.generation) not in waiter.excluded_generations
        )

    def release_read_slot(self, channel: ComputeChannel, requester_user_id: int) -> None:
        key = (channel.device_id, channel.generation, requester_user_id)
        count = self._read_reservations.get(key, 0)
        if count <= 1:
            self._read_reservations.pop(key, None)
        else:
            self._read_reservations[key] = count - 1
        self.capacity_changed()

    def replace(self, channel: ComputeChannel) -> ComputeChannel | None:
        old = self.channels.get(channel.device_id)
        if old is None and len(self.channels) >= self.max_connections:
            raise OverflowError("connection_limit")
        self.channels[channel.device_id] = channel
        if old is not None:
            self._fail_channel(old, "channel_replaced")
            for key in list(self._read_reservations):
                if key[:2] == (old.device_id, old.generation):
                    self._read_reservations.pop(key, None)
        self.capacity_changed()
        return old

    def remove(self, channel: ComputeChannel) -> None:
        if self.channels.get(channel.device_id) is channel:
            self.channels.pop(channel.device_id, None)
        self._fail_channel(channel, "channel_dropped")
        for key in list(self._read_reservations):
            if key[:2] == (channel.device_id, channel.generation):
                self._read_reservations.pop(key, None)
        self.capacity_changed()

    def _fail_channel(self, channel: ComputeChannel, reason: str) -> None:
        for lease in list(self.pending.values()):
            if lease.device_id == channel.device_id and lease.generation == channel.generation:
                self.record_outcome(channel, lease, ok=False, error=reason)
                self.finish(channel, lease)
                if not lease.future.done():
                    lease.future.set_exception(ConnectionError(reason))

    def effective_capacity(
        self, channel: ComputeChannel, operation: str = "image_resize_v1"
    ) -> int:
        if operation in {corporate_gateway_contract.OPERATION, corporate_write_contract.OPERATION}:
            if (
                channel.paused
                or channel.thermal_state == "critical"
                or (channel.memory_percent is not None and channel.memory_percent >= 90)
            ):
                return 0
            return (
                min(2, max(0, channel.corporate_read_capacity))
                if operation == corporate_gateway_contract.OPERATION
                else min(1, max(0, channel.corporate_write_capacity))
            )
        if (
            channel.paused
            or channel.thermal_state in {"serious", "critical"}
            or (channel.cpu_percent is not None and channel.cpu_percent >= 70)
            or (channel.memory_percent is not None and channel.memory_percent >= 80)
            or channel.power == "low_power"
        ):
            return 0
        return min(2, max(0, channel.capacity))

    def active_jobs(self, channel: ComputeChannel, operation: str = "image_resize_v1") -> int:
        corporate = {corporate_gateway_contract.OPERATION, corporate_write_contract.OPERATION}
        leases = [
            item
            for item in self.pending.values()
            if item.device_id == channel.device_id and item.generation == channel.generation
        ]
        if operation in corporate:
            reported = (
                channel.reported_corporate_active_jobs
                if operation == corporate_gateway_contract.OPERATION
                else channel.reported_corporate_active_writes
            )
            reservations = (
                sum(
                    count
                    for (device_id, generation, _), count in self._read_reservations.items()
                    if (device_id, generation) == (channel.device_id, channel.generation)
                )
                if operation == corporate_gateway_contract.OPERATION
                else 0
            )
            return max(reported, sum(item.operation == operation for item in leases) + reservations)
        return max(
            channel.reported_active_jobs,
            max(0, channel.inflight - sum(item.operation in corporate for item in leases)),
        )

    def record_outcome(self, channel: ComputeChannel, lease: ComputeLease, *, ok: bool, error=None):
        if lease.health_recorded:
            return
        lease.health_recorded = True
        self.routing_health.observe(
            channel.device_id,
            lease.routing_key or lease.operation,
            ok=ok,
            elapsed=max(0, time.monotonic() - lease.started_at),
            payload_bytes=lease.direct_payload_size if lease.direct else lease.payload_size,
            error=error,
        )

    def choose(
        self,
        operation: str,
        *,
        excluded: set[str] | None = None,
        required_supports: set[str] | None = None,
        eligible_owner_ids: set[int] | frozenset[int] | None = None,
        routing_key: str | None = None,
    ) -> ComputeChannel | None:
        now = time.monotonic()
        excluded = excluded or set()
        candidates = [
            item
            for item in self.channels.values()
            if item.device_id not in excluded
            and (eligible_owner_ids is None or item.owner_user_id in eligible_owner_ids)
            and item.ready
            and operation in item.supports
            and (required_supports or set()).issubset(item.supports)
            and (
                "corporate_inventory_read_v1" not in (required_supports or set())
                or item.corporate_inventory_status in {"ready", "unverified_host_acl"}
            )
            and self.routing_health.available(item.device_id, routing_key or operation)
            and now - item.last_heartbeat <= self.readiness_timeout_seconds
            and self.active_jobs(item, operation) < self.effective_capacity(item, operation)
        ]
        if not candidates:
            return None

        def score(item: ComputeChannel) -> tuple[float, float, float, float, str]:
            capacity = self.effective_capacity(item, operation)
            occupancy = self.active_jobs(item, operation) / capacity
            cpu_pressure = 0.75 if item.cpu_percent is None else min(1.0, item.cpu_percent / 70)
            memory_pressure = (
                0.75 if item.memory_percent is None else min(1.0, item.memory_percent / 80)
            )
            resource_pressure = max(cpu_pressure, memory_pressure)
            return (
                occupancy,
                resource_pressure,
                self.routing_health.latency(item.device_id, routing_key or operation),
                item.last_selected,
                item.device_id,
            )

        return min(candidates, key=score)

    def new_lease(
        self,
        channel: ComputeChannel,
        *,
        operation: str,
        payload_bytes: bytes,
        requester_user_id: int,
        deadline_monotonic: float,
        routing_key: str | None = None,
    ) -> ComputeLease:
        if operation not in OPERATIONS:
            raise ValueError("unsupported_operation")
        corporate_read = operation == corporate_gateway_contract.OPERATION
        scoped = [
            item
            for item in self.pending.values()
            if (item.operation == corporate_gateway_contract.OPERATION) == corporate_read
        ]
        pending_limit = self.max_corporate_pending if corporate_read else self.max_pending
        requester_limit = (
            self.max_corporate_requester_pending if corporate_read else self.max_requester_pending
        )
        payload_used = (
            self.corporate_pending_payload_bytes if corporate_read else self.pending_payload_bytes
        )
        payload_limit = (
            self.max_corporate_payload_bytes if corporate_read else self.max_payload_bytes
        )
        if len(scoped) >= pending_limit:
            raise OverflowError("pending_limit")
        requester_pending = sum(item.requester_user_id == requester_user_id for item in scoped)
        if requester_pending >= requester_limit:
            raise OverflowError("requester_pending_limit")
        if payload_used + len(payload_bytes) > payload_limit:
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
            routing_key=routing_key or operation,
        )
        self.pending[(lease.device_id, lease.generation, lease.job_id, lease.attempt_id)] = lease
        if corporate_read:
            self.corporate_pending_payload_bytes += lease.payload_size
        else:
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
        heavy = [
            item
            for item in self.pending.values()
            if item.operation != corporate_gateway_contract.OPERATION
        ]
        if len(heavy) >= self.max_pending:
            raise OverflowError("pending_limit")
        requester_pending = sum(item.requester_user_id == requester_user_id for item in heavy)
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
            if lease.operation == corporate_gateway_contract.OPERATION:
                self.corporate_pending_payload_bytes = max(
                    0, self.corporate_pending_payload_bytes - lease.payload_size
                )
            else:
                self.pending_payload_bytes = max(0, self.pending_payload_bytes - lease.payload_size)
            channel.inflight = max(0, channel.inflight - 1)
            self.capacity_changed()

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
        channel = self.channels.get(device_id)
        if channel is not None and channel.generation == generation:
            self.record_outcome(
                channel, lease, ok=result.get("ok") is True, error=result.get("error")
            )
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
            active_jobs = self.active_jobs(item)
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
                    "inflight": active_jobs,
                    "active_jobs": active_jobs,
                    "cpu_percent": item.cpu_percent if fresh else None,
                    "memory_percent": item.memory_percent if fresh else None,
                    "thermal_state": item.thermal_state if fresh else None,
                    "power": item.power,
                    "supports": sorted(item.supports),
                    "corporate_read_capacity": self.effective_capacity(
                        item, corporate_gateway_contract.OPERATION
                    )
                    if fresh
                    else 0,
                    "corporate_write_capacity": self.effective_capacity(
                        item, corporate_write_contract.OPERATION
                    )
                    if fresh
                    else 0,
                    "corporate_inventory_status": item.corporate_inventory_status,
                    "route_health": self.routing_health.snapshot(item.device_id),
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
    if operation == corporate_gateway_contract.OPERATION:
        try:
            return corporate_gateway_contract.validate_result_shape(result)
        except corporate_gateway_contract.ContractError:
            return None
    if operation == corporate_write_contract.OPERATION:
        try:
            return corporate_write_contract.validate_result_shape(result)
        except corporate_write_contract.ContractError:
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
