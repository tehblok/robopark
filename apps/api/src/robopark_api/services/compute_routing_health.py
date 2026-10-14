"""Bounded, operation-specific observations for an otherwise stateless scheduler.

Transport failures affect only the affected device/operation. Authorization and
resource errors are returned to the caller, not reclassified as a broken route.
There are no background probes: a cooled route becomes eligible on demand.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class RouteObservation:
    completed: int = 0
    transport_failures: int = 0
    consecutive_failures: int = 0
    latency_seconds: float | None = None
    throughput_bytes_per_second: float | None = None
    cooldown_until: float = 0.0
    updated_at: float = 0.0


class RoutingHealth:
    def __init__(self, *, now: Callable[[], float] = time.monotonic, max_entries=4096, ttl=600.0):
        self.now = now
        self.max_entries = max_entries
        self.ttl = ttl
        self._observations: OrderedDict[tuple[str, str], RouteObservation] = OrderedDict()

    def _prune(self):
        now = self.now()
        while self._observations:
            key, item = next(iter(self._observations.items()))
            if len(self._observations) <= self.max_entries and now - item.updated_at < self.ttl:
                break
            self._observations.pop(key)

    def observe(
        self,
        device_id: str,
        operation: str,
        *,
        ok: bool,
        elapsed: float,
        payload_bytes: int = 0,
        error: str | None = None,
    ):
        if not device_id or not operation or not math.isfinite(elapsed) or elapsed < 0:
            return
        # Not-found/permission/rate-limit errors describe the request or access.
        # They must not make the server try a more privileged fallback route.
        transport_failure = error in {"network", "timeout", "channel_dropped", "channel_replaced"}
        if not ok and not transport_failure:
            return
        key = (device_id, operation)
        self._prune()
        item = self._observations.get(key) or RouteObservation()
        item.updated_at = self.now()
        if ok:
            item.completed += 1
            item.consecutive_failures = 0
            item.cooldown_until = 0.0
            measured = min(120.0, max(0.001, elapsed))
            item.latency_seconds = (
                measured
                if item.latency_seconds is None
                else 0.75 * item.latency_seconds + 0.25 * measured
            )
            if 0 < payload_bytes <= 64 * 1024 * 1024:
                speed = payload_bytes / measured
                item.throughput_bytes_per_second = (
                    speed
                    if item.throughput_bytes_per_second is None
                    else 0.75 * item.throughput_bytes_per_second + 0.25 * speed
                )
        else:
            item.transport_failures += 1
            item.consecutive_failures = min(8, item.consecutive_failures + 1)
            item.cooldown_until = item.updated_at + min(
                60.0, 10.0 * 2 ** (item.consecutive_failures - 1)
            )
        self._observations[key] = item
        self._observations.move_to_end(key)
        self._prune()

    def available(self, device_id: str, operation: str) -> bool:
        self._prune()
        item = self._observations.get((device_id, operation))
        return item is None or item.cooldown_until <= self.now()

    def latency(self, device_id: str, operation: str) -> float:
        self._prune()
        item = self._observations.get((device_id, operation))
        # A modest neutral prior gives new/reconnected devices a chance to run.
        return item.latency_seconds if item and item.latency_seconds is not None else 1.0

    def snapshot(self, device_id: str) -> dict:
        self._prune()
        now = self.now()
        return {
            operation: {
                "completed": item.completed,
                "transport_failures": item.transport_failures,
                "latency_ms": round(item.latency_seconds * 1000)
                if item.latency_seconds is not None
                else None,
                "throughput_bytes_per_second": round(item.throughput_bytes_per_second)
                if item.throughput_bytes_per_second is not None
                else None,
                "retry_after_seconds": max(0, math.ceil(item.cooldown_until - now)),
            }
            for (node, operation), item in self._observations.items()
            if node == device_id
        }
