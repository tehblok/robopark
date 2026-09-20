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

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from robopark_api.services.cache_metrics import CacheMetricsSnapshot, family
from robopark_api.services.cache_policy import CachePolicy
from robopark_api.services.live_merge import LiveMergeStore, get_live_merge_store

T = TypeVar("T")

_MISSING: object = object()
DEFAULT_MAX_ENTRIES = 1024
DEFAULT_MAX_STALE_SECONDS = 60.0
DEFAULT_MAX_BYTES = 16 * 1024 * 1024


@dataclass
class _Flight(Generic[T]):  # noqa: UP046
    done: threading.Event = field(default_factory=threading.Event)
    value: object = _MISSING
    error: BaseException | None = None
    generation: tuple[int, int] = (0, 0)
    retired: bool = False


class ResponseCache(Generic[T]):  # noqa: UP046
    """Thread-safe TTL cache. Concurrent misses for the same key share one loader call."""

    def __init__(
        self,
        ttl_seconds: float | None = None,
        *,
        name: str = "cache",
        shared: LiveMergeStore | None | bool = True,
        shared_payload: Callable[[T], Any] | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_stale_seconds: float = DEFAULT_MAX_STALE_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        policy: CachePolicy | None = None,
    ) -> None:
        if policy is not None:
            ttl_seconds = policy.ttl_seconds
            max_stale_seconds = policy.stale_seconds
            max_entries = policy.max_entries
            max_bytes = policy.max_bytes
            if policy.persistence == "memory":
                shared = False
        if ttl_seconds is None:
            raise ValueError("ttl_seconds must be provided")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if max_stale_seconds <= 0:
            raise ValueError("max_stale_seconds must be positive")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._ttl = ttl_seconds
        self._name = name
        self._max_entries = max_entries
        self._max_stale = max_stale_seconds
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._store: OrderedDict[str, tuple[float, T, float | None, int]] = OrderedDict()
        self._bytes = 0
        self._metrics = family(name)
        self._flights: dict[str, _Flight[T]] = {}
        self._generation = 0
        self._key_generations: dict[str, int] = {}
        # True → look up the process-wide store each call (tests may disable it).
        # LiveMergeStore → always that store. False/None → in-process only.
        self._shared: LiveMergeStore | None | bool = shared
        self._shared_payload = shared_payload

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

    def _l1_valid(self, key: str, hit: tuple[float, T, float | None, int], now: float) -> bool:
        if now - hit[0] >= self._ttl:
            return False
        merge = self._merge()
        if merge is None:
            return True
        return hit[2] is not None and merge.result_mtime(self._name, key) == hit[2]

    def _prune_expired_locked(self, now: float) -> None:
        retention = max(self._ttl, self._max_stale)
        expired = [key for key, hit in self._store.items() if now - hit[0] >= retention]
        for key in expired:
            self._remove_locked(key)

    @staticmethod
    def _entry_bytes(value: object) -> int:
        try:
            return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return len(repr(value).encode("utf-8"))

    def _remove_locked(self, key: str) -> None:
        removed = self._store.pop(key, None)
        if removed is not None:
            self._bytes = max(0, self._bytes - removed[3])

    def _update_gauges_locked(self) -> None:
        self._metrics.gauge(entries=len(self._store), bytes_=self._bytes)

    def _store_locked(self, key: str, hit: tuple[float, T, float | None]) -> None:
        self._remove_locked(key)
        sized = (*hit, self._entry_bytes(hit[1]))
        self._store[key] = sized
        self._bytes += sized[3]
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries or self._bytes > self._max_bytes:
            oldest = next(iter(self._store))
            self._remove_locked(oldest)
            self._metrics.increment("evictions")
        self._update_gauges_locked()

    @staticmethod
    def _shared_loaded_at(mtime: float | None, now: float) -> float:
        if mtime is None:
            return now
        return now - max(0.0, time.time() - mtime)

    def invalidate(self, key: str, *, reason: str = "key") -> None:
        with self._lock:
            flight = self._flights.pop(key, None)
            if flight is not None:
                flight.retired = True
                self._key_generations[key] = self._key_generations.get(key, 0) + 1
            else:
                self._key_generations.pop(key, None)
            self._remove_locked(key)
            self._update_gauges_locked()
        self._metrics.invalidate(reason)
        merge = self._merge()
        if merge is not None:
            merge.invalidate(self._name, key)

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            for key in set(self._store) | set(self._flights):
                if key.startswith(prefix):
                    flight = self._flights.pop(key, None)
                    if flight is not None:
                        flight.retired = True
                    self._key_generations[key] = self._key_generations.get(key, 0) + 1
            stale = [k for k in self._store if k.startswith(prefix)]
            for key in stale:
                self._remove_locked(key)
            self._update_gauges_locked()
        if stale:
            self._metrics.invalidate("prefix", len(stale))
        merge = self._merge()
        if merge is not None:
            merge.invalidate_prefix(self._name, prefix)

    def clear(self, *, reason: str = "namespace") -> None:
        with self._lock:
            for flight in self._flights.values():
                flight.retired = True
            self._flights.clear()
            self._generation += 1
            self._key_generations.clear()
            self._store.clear()
            self._bytes = 0
            self._update_gauges_locked()
        self._metrics.invalidate(reason)
        merge = self._merge()
        if merge is not None:
            merge.clear_namespace(self._name, reason=reason)

    def peek(self, key: str) -> T | None:
        """Return the current cached value regardless of TTL (for tests)."""
        with self._lock:
            hit = self._store.get(key)
            return hit[1] if hit is not None else None

    def metrics(self) -> CacheMetricsSnapshot:
        return self._metrics.snapshot()

    def get_if_fresh(self, key: str) -> tuple[bool, T | None]:
        """Return a fresh local or shared value without invoking a loader."""
        now = time.monotonic()
        merge = self._merge()
        with self._lock:
            self._prune_expired_locked(now)
            hit = self._store.get(key)
            if hit is not None and self._l1_valid(key, hit, now):
                self._store.move_to_end(key)
                self._metrics.increment("hits")
                return True, hit[1]
            if hit is not None:
                self._remove_locked(key)

        if merge is None:
            return False, None
        found, blob = merge.try_fresh(self._name, key, self._ttl)
        if not found:
            self._metrics.increment("misses")
            return False, None
        mtime = merge.result_mtime(self._name, key)
        with self._lock:
            loaded_at = self._shared_loaded_at(mtime, time.monotonic())
            self._store_locked(key, (loaded_at, blob, mtime))
        self._metrics.increment("hits")
        return True, blob  # type: ignore[return-value]

    def get_or_load(self, key: str, loader: Callable[[], T]) -> T:
        from robopark_api.db import release_request_session

        now = time.monotonic()
        merge = self._merge()
        stale: tuple[float, T, float | None, int] | None = None
        with self._lock:
            self._prune_expired_locked(now)
            hit = self._store.get(key)
            if hit is not None and self._l1_valid(key, hit, now):
                self._store.move_to_end(key)
                self._metrics.increment("hits")
                return hit[1]
            if hit is not None:
                stale = hit
                self._remove_locked(key)

        if merge is not None:
            found, blob = merge.try_fresh(self._name, key, self._ttl)
            if found:
                mtime = merge.result_mtime(self._name, key)
                with self._lock:
                    loaded_at = self._shared_loaded_at(mtime, time.monotonic())
                    self._store_locked(key, (loaded_at, blob, mtime))
                self._metrics.increment("hits")
                return blob

        self._metrics.increment("misses")

        with self._lock:
            # A fast leader may have completed between the first lookup and
            # flight registration. Recheck under the same lock that owns the
            # flight map so a late caller cannot start a duplicate load.
            raced_hit = self._store.get(key)
            if raced_hit is not None and self._l1_valid(key, raced_hit, time.monotonic()):
                self._store.move_to_end(key)
                self._metrics.increment("hits")
                return raced_hit[1]
            flight = self._flights.get(key)
            is_leader = flight is None
            if is_leader:
                flight = _Flight[T](
                    generation=(self._generation, self._key_generations.get(key, 0))
                )
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
        load_started = time.monotonic()
        load_had_error = False
        try:
            if merge is not None:
                value = merge.merge_load(
                    self._name,
                    key,
                    self._ttl,
                    loader,
                    shared_payload=self._shared_payload,
                    max_stale_seconds=self._max_stale,
                )
            else:
                value = loader()
        except BaseException as exc:  # last-good or fan-out one shared error
            load_had_error = True
            if (
                isinstance(exc, Exception)
                and stale is not None
                and time.monotonic() - stale[0] < self._max_stale
            ):
                value = stale[1]
            else:
                error = exc
                self._metrics.observe_load(time.monotonic() - load_started, error=True)
                raise
        finally:
            with self._lock:
                flight.value = value
                flight.error = error
                current_generation = (
                    self._generation,
                    self._key_generations.get(key, 0),
                )
                if (
                    error is None
                    and value is not _MISSING
                    and not flight.retired
                    and flight.generation == current_generation
                ):
                    mtime = merge.result_mtime(self._name, key) if merge is not None else None
                    stored_at = (
                        stale[0]
                        if stale is not None and value is stale[1]
                        else self._shared_loaded_at(mtime, time.monotonic())
                    )
                    self._store_locked(  # type: ignore[arg-type]
                        key,
                        (stored_at, value, mtime),
                    )
                if self._flights.get(key) is flight:
                    self._flights.pop(key, None)
                    self._key_generations.pop(key, None)
                elif key not in self._flights:
                    self._key_generations.pop(key, None)
                flight.done.set()

        self._metrics.observe_load(time.monotonic() - load_started, error=load_had_error)

        assert value is not _MISSING
        return value  # type: ignore[return-value]
