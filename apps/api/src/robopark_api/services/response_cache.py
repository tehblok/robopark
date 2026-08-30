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

When a :class:`~robopark_api.services.live_merge.LiveMergeStore` is attached,
identical keys also merge across processes via a result blob. The file lock is
never held during the loader (upstream HTTP).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from robopark_api.services.live_merge import LiveMergeStore, get_live_merge_store

T = TypeVar("T")

_MISSING: object = object()


@dataclass
class _Flight(Generic[T]):  # noqa: UP046
    done: threading.Event = field(default_factory=threading.Event)
    value: object = _MISSING
    error: BaseException | None = None


class ResponseCache(Generic[T]):  # noqa: UP046
    """Thread-safe TTL cache. Concurrent misses for the same key share one loader call."""

    def __init__(
        self,
        ttl_seconds: float,
        *,
        name: str = "cache",
        shared: LiveMergeStore | None | bool = True,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl = ttl_seconds
        self._name = name
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, T, float | None]] = {}
        self._flights: dict[str, _Flight[T]] = {}
        # True → look up the process-wide store each call (tests may disable it).
        # LiveMergeStore → always that store. False/None → in-process only.
        self._shared: LiveMergeStore | None | bool = shared

    @property
    def name(self) -> str:
        return self._name

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def _merge(self) -> LiveMergeStore | None:
        if self._shared is False or self._shared is None:
            return None
        if isinstance(self._shared, LiveMergeStore):
            return self._shared
        return get_live_merge_store()

    def _l1_valid(self, key: str, hit: tuple[float, T, float | None], now: float) -> bool:
        if now - hit[0] >= self._ttl:
            return False
        merge = self._merge()
        if merge is None:
            return True
        return merge.result_mtime(self._name, key) == hit[2]

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)
        merge = self._merge()
        if merge is not None:
            merge.invalidate(self._name, key)

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            stale = [k for k in self._store if k.startswith(prefix)]
            for key in stale:
                self._store.pop(key, None)
        merge = self._merge()
        if merge is not None:
            merge.invalidate_prefix(self._name, prefix)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._flights.clear()
        merge = self._merge()
        if merge is not None:
            merge.clear_namespace(self._name)

    def peek(self, key: str) -> T | None:
        """Return the current cached value regardless of TTL (for tests)."""
        with self._lock:
            hit = self._store.get(key)
            return hit[1] if hit is not None else None

    def get_or_load(self, key: str, loader: Callable[[], T]) -> T:
        from robopark_api.db import release_request_session

        now = time.monotonic()
        merge = self._merge()
        stale: T | None = None
        with self._lock:
            hit = self._store.get(key)
            if hit is not None and self._l1_valid(key, hit, now):
                return hit[1]
            if hit is not None:
                stale = hit[1]
                self._store.pop(key, None)

        if merge is not None:
            found, blob = merge.try_fresh(self._name, key, self._ttl)
            if found:
                mtime = merge.result_mtime(self._name, key)
                with self._lock:
                    self._store[key] = (time.monotonic(), blob, mtime)
                return blob

        with self._lock:
            flight = self._flights.get(key)
            is_leader = flight is None
            if is_leader:
                flight = _Flight[T]()
                self._flights[key] = flight

        release_request_session()
        if not is_leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            assert flight.value is not _MISSING
            return flight.value  # type: ignore[return-value]

        value: object = _MISSING
        error: BaseException | None = None
        try:
            if merge is not None:
                value = merge.merge_load(self._name, key, self._ttl, loader)
            else:
                value = loader()
        except BaseException as exc:  # last-good or fan-out one shared error
            if stale is not None:
                value = stale
            else:
                error = exc
                raise
        finally:
            with self._lock:
                flight.value = value
                flight.error = error
                if error is None and value is not _MISSING:
                    mtime = merge.result_mtime(self._name, key) if merge is not None else None
                    self._store[key] = (time.monotonic(), value, mtime)  # type: ignore[assignment]
                self._flights.pop(key, None)
                flight.done.set()

        assert value is not _MISSING
        return value  # type: ignore[return-value]
