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
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from robopark_api.services.live_merge import LiveMergeStore, get_live_merge_store

T = TypeVar("T")

_MISSING: object = object()
DEFAULT_MAX_ENTRIES = 1024
DEFAULT_MAX_STALE_SECONDS = 60.0


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
        shared_payload: Callable[[T], Any] | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_stale_seconds: float = DEFAULT_MAX_STALE_SECONDS,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if max_stale_seconds <= 0:
            raise ValueError("max_stale_seconds must be positive")
        self._ttl = ttl_seconds
        self._name = name
        self._max_entries = max_entries
        self._max_stale = max_stale_seconds
        self._lock = threading.Lock()
        self._store: OrderedDict[str, tuple[float, T, float | None]] = OrderedDict()
        self._flights: dict[str, _Flight[T]] = {}
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

    def _l1_valid(self, key: str, hit: tuple[float, T, float | None], now: float) -> bool:
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
            self._store.pop(key, None)

    def _store_locked(self, key: str, hit: tuple[float, T, float | None]) -> None:
        self._store[key] = hit
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    @staticmethod
    def _shared_loaded_at(mtime: float | None, now: float) -> float:
        if mtime is None:
            return now
        return now - max(0.0, time.time() - mtime)

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

    def get_if_fresh(self, key: str) -> tuple[bool, T | None]:
        """Return a fresh local or shared value without invoking a loader."""
        now = time.monotonic()
        merge = self._merge()
        with self._lock:
            self._prune_expired_locked(now)
            hit = self._store.get(key)
            if hit is not None and self._l1_valid(key, hit, now):
                self._store.move_to_end(key)
                return True, hit[1]
            if hit is not None:
                self._store.pop(key, None)

        if merge is None:
            return False, None
        found, blob = merge.try_fresh(self._name, key, self._ttl)
        if not found:
            return False, None
        mtime = merge.result_mtime(self._name, key)
        with self._lock:
            loaded_at = self._shared_loaded_at(mtime, time.monotonic())
            self._store_locked(key, (loaded_at, blob, mtime))
        return True, blob  # type: ignore[return-value]

    def get_or_load(self, key: str, loader: Callable[[], T]) -> T:
        from robopark_api.db import release_request_session

        now = time.monotonic()
        merge = self._merge()
        stale: tuple[float, T, float | None] | None = None
        with self._lock:
            self._prune_expired_locked(now)
            hit = self._store.get(key)
            if hit is not None and self._l1_valid(key, hit, now):
                self._store.move_to_end(key)
                return hit[1]
            if hit is not None:
                stale = hit
                self._store.pop(key, None)

        if merge is not None:
            found, blob = merge.try_fresh(self._name, key, self._ttl)
            if found:
                mtime = merge.result_mtime(self._name, key)
                with self._lock:
                    loaded_at = self._shared_loaded_at(mtime, time.monotonic())
                    self._store_locked(key, (loaded_at, blob, mtime))
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
                value = merge.merge_load(
                    self._name,
                    key,
                    self._ttl,
                    loader,
                    shared_payload=self._shared_payload,
                )
            else:
                value = loader()
        except BaseException as exc:  # last-good or fan-out one shared error
            if (
                isinstance(exc, Exception)
                and stale is not None
                and time.monotonic() - stale[0] < self._max_stale
            ):
                value = stale[1]
            else:
                error = exc
                raise
        finally:
            with self._lock:
                flight.value = value
                flight.error = error
                if error is None and value is not _MISSING:
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
                self._flights.pop(key, None)
                flight.done.set()

        assert value is not _MISSING
        return value  # type: ignore[return-value]
