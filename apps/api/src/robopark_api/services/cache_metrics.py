"""Small dependency-free cache metrics registry consumed by host health later."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class CacheMetricsSnapshot:
    hits: int
    misses: int
    loads: int
    errors: int
    evictions: int
    bytes: int
    entries: int
    refresh_latency_seconds: float
    invalidations: int


class CacheMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters = dict.fromkeys(
            ("hits", "misses", "loads", "errors", "evictions", "invalidations"), 0
        )
        self._bytes = 0
        self._entries = 0
        self._latency = 0.0

    def increment(self, field: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[field] += amount

    def observe_load(self, latency: float, *, error: bool = False) -> None:
        with self._lock:
            self._counters["loads"] += 1
            if error:
                self._counters["errors"] += 1
            self._latency = max(0.0, latency)

    def gauge(self, *, entries: int, bytes_: int) -> None:
        with self._lock:
            self._entries = entries
            self._bytes = bytes_

    def snapshot(self) -> CacheMetricsSnapshot:
        with self._lock:
            return CacheMetricsSnapshot(
                **self._counters,
                bytes=self._bytes,
                entries=self._entries,
                refresh_latency_seconds=self._latency,
            )


_registry_lock = threading.Lock()
_registry: dict[str, CacheMetrics] = {}


def family(name: str) -> CacheMetrics:
    with _registry_lock:
        return _registry.setdefault(name, CacheMetrics())


def snapshot_all() -> dict[str, dict[str, int | float]]:
    with _registry_lock:
        items = tuple(_registry.items())
    return {name: asdict(metrics.snapshot()) for name, metrics in items}
