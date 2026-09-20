"""Bounded cross-worker locks for idempotent local workflows."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool

try:  # Robopark is deployed on Linux; keep unit tests portable.
    import fcntl
except ImportError:  # pragma: no cover - non-Unix development fallback
    fcntl = None


LOCK_WAIT_SECONDS = 5.0
POSTGRES_LOCK_POOL_SIZE = 2
_SLEEP_SECONDS = 0.02
SQLITE_LOCK_BUCKETS = 64


@dataclass
class _ProcessLock:
    lock: threading.Lock
    users: int = 0


@dataclass(frozen=True)
class _PostgresLockEngine:
    engine: Engine
    slots: threading.BoundedSemaphore


_process_locks: dict[str, _ProcessLock] = {}
_process_locks_guard = threading.Lock()
_postgres_lock_engines: dict[str, _PostgresLockEngine] = {}
_postgres_lock_engines_guard = threading.Lock()
_postgres_connect_deadline = threading.local()


class _LockDeadlineExceeded(TimeoutError):
    pass


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _acquire_process_lock(key: str) -> _ProcessLock:
    with _process_locks_guard:
        entry = _process_locks.get(key)
        if entry is None:
            entry = _ProcessLock(lock=threading.Lock())
            _process_locks[key] = entry
        entry.users += 1
    if not entry.lock.acquire(timeout=LOCK_WAIT_SECONDS):
        _release_process_lock(key, entry, acquired=False)
        raise HTTPException(503, "idempotency_lock_busy")
    return entry


def _release_process_lock(key: str, entry: _ProcessLock, *, acquired: bool = True) -> None:
    if acquired:
        entry.lock.release()
    with _process_locks_guard:
        entry.users -= 1
        if entry.users == 0 and _process_locks.get(key) is entry:
            del _process_locks[key]


def _sqlite_lock_path(bind: Any, key: str) -> Path | None:
    database = getattr(getattr(bind, "url", None), "database", None)
    if not database or database == ":memory:":
        return None
    root = Path(database).parent / ".robopark-idempotency-locks"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    bucket = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % SQLITE_LOCK_BUCKETS
    return root / f"bucket-{bucket:02d}.lock"


@contextmanager
def _sqlite_file_lock(bind: Any, key: str) -> Iterator[None]:
    """Use a prunable process lock plus `flock` for separate SQLite workers."""
    entry = _acquire_process_lock(key)
    descriptor: int | None = None
    try:
        path = _sqlite_lock_path(bind, key)
        if path is not None and fcntl is not None:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise HTTPException(503, "idempotency_lock_busy") from None
                    time.sleep(_SLEEP_SECONDS)
        yield
    finally:
        if descriptor is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        _release_process_lock(key, entry)


def _postgres_lock_engine(bind: Any) -> _PostgresLockEngine:
    """Return a small lock-only pool which never waits for the request pool."""
    url = bind.url.render_as_string(hide_password=False)
    cache_key = hashlib.sha256(url.encode()).hexdigest()
    with _postgres_lock_engines_guard:
        state = _postgres_lock_engines.get(cache_key)
        if state is None:
            engine = create_engine(
                url,
                future=True,
                poolclass=QueuePool,
                pool_size=POSTGRES_LOCK_POOL_SIZE,
                max_overflow=0,
                pool_timeout=LOCK_WAIT_SECONDS,
                pool_pre_ping=False,
                pool_recycle=0,
                pool_use_lifo=True,
            )

            @event.listens_for(engine, "do_connect")
            def _bound_physical_connect(_dialect, _record, _cargs, cparams) -> None:
                deadline = getattr(_postgres_connect_deadline, "value", None)
                if deadline is None:
                    deadline = time.monotonic() + LOCK_WAIT_SECONDS
                remaining = _remaining_seconds(deadline)
                connect_seconds = int(remaining)
                timeout_milliseconds = int(remaining * 1000)
                if connect_seconds < 1 or timeout_milliseconds < 1:
                    raise _LockDeadlineExceeded
                cparams["connect_timeout"] = connect_seconds
                cparams["tcp_user_timeout"] = timeout_milliseconds
                deadline_options = (
                    f"-c statement_timeout={timeout_milliseconds} "
                    f"-c lock_timeout={timeout_milliseconds}"
                )
                existing_options = str(cparams.get("options", "")).strip()
                cparams["options"] = " ".join(
                    option for option in (existing_options, deadline_options) if option
                )

            state = _PostgresLockEngine(
                engine=engine,
                slots=threading.BoundedSemaphore(POSTGRES_LOCK_POOL_SIZE),
            )
            _postgres_lock_engines[cache_key] = state
        return state


def dispose_database_lock_engines() -> None:
    """Release cached lock-engine resources during application shutdown."""
    with _postgres_lock_engines_guard:
        engines = list(_postgres_lock_engines.values())
        _postgres_lock_engines.clear()
    for state in engines:
        state.engine.dispose()


@contextmanager
def database_idempotency_lock(db: Any, key: str) -> Iterator[None]:
    """Hold a bounded idempotency lock across commits and request re-binding.

    PostgreSQL uses a small independent pool and a transaction-scoped advisory
    lock. It never consumes a request-pool connection, which may be released
    and later replaced around upstream waits. SQLite keeps a keyed in-process
    lock and a bounded set of non-blocking `flock` sidecars for coordination
    across its separate host worker processes.
    """
    bind = db.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        state = _postgres_lock_engine(bind)
        if not state.slots.acquire(timeout=_remaining_seconds(deadline)):
            raise HTTPException(503, "idempotency_lock_busy")
        try:
            if _remaining_seconds(deadline) <= 0:
                raise HTTPException(503, "idempotency_lock_busy")
            _postgres_connect_deadline.value = deadline
            try:
                with state.engine.connect() as connection:
                    if _remaining_seconds(deadline) <= 0:
                        raise HTTPException(503, "idempotency_lock_busy")
                    with connection.begin():
                        while True:
                            if _remaining_seconds(deadline) <= 0:
                                raise HTTPException(503, "idempotency_lock_busy")
                            acquired = connection.scalar(
                                text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                                {"key": key},
                            )
                            remaining = _remaining_seconds(deadline)
                            if remaining <= 0:
                                raise HTTPException(503, "idempotency_lock_busy")
                            if acquired:
                                break
                            time.sleep(min(_SLEEP_SECONDS, remaining))
                        if _remaining_seconds(deadline) <= 0:
                            raise HTTPException(503, "idempotency_lock_busy")
                        yield
            except _LockDeadlineExceeded:
                raise HTTPException(503, "idempotency_lock_busy") from None
            finally:
                _postgres_connect_deadline.value = None
        finally:
            state.slots.release()
        return
    if dialect == "sqlite":
        with _sqlite_file_lock(bind, key):
            yield
        return
    with _sqlite_file_lock(bind, key):
        yield
