"""Generic in-process TTL cache with single-flight de-duplication.

Multiple Robopark users typically request the same Tracker/Emergency data
within seconds of each other. Without a shared cache each request pays the
full upstream round-trip; with the cache the first caller does the work and
everyone else — including concurrent callers — reuses the result until it
expires. Modelled on the pattern in ``emergency_cache.py`` but generic over
key and payload.

Threading model: FastAPI runs sync endpoints on a threadpool, so the primitives
here are ``threading``-based (not asyncio). Values are treated as opaque
references; callers are responsible for making them safe to share (e.g. by
storing dicts/lists rather than SQLAlchemy sessions).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")

_MISSING: object = object()


@dataclass
class _Flight(Generic[T]):
    done: threading.Event = field(default_factory=threading.Event)
    value: object = _MISSING
    error: BaseException | None = None


class ResponseCache(Generic[T]):
    """Thread-safe TTL cache. Concurrent misses for the same key share one loader call."""

    def __init__(self, ttl_seconds: float, *, name: str = "cache") -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._name = name
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, T]] = {}
        self._flights: dict[str, _Flight[T]] = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            stale = [k for k in self._store if k.startswith(prefix)]
            for key in stale:
                self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._flights.clear()

    def peek(self, key: str) -> T | None:
        """Return the current cached value regardless of TTL (for tests)."""
        with self._lock:
            hit = self._store.get(key)
            return hit[1] if hit is not None else None

    def get_or_load(self, key: str, loader: Callable[[], T]) -> T:
        now = time.monotonic()
        with self._lock:
            hit = self._store.get(key)
            if hit is not None and now - hit[0] < self._ttl:
                return hit[1]
            if hit is not None:
                self._store.pop(key, None)
            flight = self._flights.get(key)
            is_leader = flight is None
            if is_leader:
                flight = _Flight[T]()
                self._flights[key] = flight

        if not is_leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            assert flight.value is not _MISSING
            return flight.value  # type: ignore[return-value]

        value: object = _MISSING
        error: BaseException | None = None
        try:
            value = loader()
        except BaseException as exc:  # propagate to every waiter
            error = exc
            raise
        finally:
            with self._lock:
                flight.value = value
                flight.error = error
                if error is None and value is not _MISSING:
                    self._store[key] = (time.monotonic(), value)  # type: ignore[assignment]
                self._flights.pop(key, None)
                flight.done.set()

        assert value is not _MISSING
        return value  # type: ignore[return-value]
