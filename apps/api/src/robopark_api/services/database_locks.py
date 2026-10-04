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
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.pool import QueuePool

try:  # Robopark is deployed on Linux; keep unit tests portable.
    import fcntl
except ImportError:  # pragma: no cover - non-Unix development fallback
    fcntl = None


LOCK_WAIT_SECONDS = 5.0
POSTGRES_LOCK_POOL_SIZE = 16
_SLEEP_SECONDS = 0.02
SQLITE_LOCK_BUCKETS = 64


@dataclass
class _ProcessLock:
    lock: Any
    users: int = 0


@dataclass(frozen=True)
class _PostgresLockEngine:
    engine: Engine
    slots: threading.BoundedSemaphore


@dataclass(frozen=True)
class _PostgresPhaseTimeouts:
    connect_milliseconds: int
    query_milliseconds: int
    safety_milliseconds: int
    attempt_milliseconds: int

    @property
    def connect_seconds(self) -> int:
        return self.connect_milliseconds // 1000


@dataclass(frozen=True)
class _PostgresPhaseDeadlines:
    connect: float
    query: float
    total: float


_process_locks: dict[str, _ProcessLock] = {}
_process_locks_guard = threading.Lock()
_sqlite_thread_locks = threading.local()
_postgres_lock_engines: dict[str, _PostgresLockEngine] = {}
_postgres_lock_engines_guard = threading.Lock()
_postgres_connect_deadline = threading.local()
_postgres_active_scope = threading.local()


class _LockDeadlineExceeded(TimeoutError):
    pass


def _remaining_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _postgres_phase_timeouts(total_seconds: float) -> _PostgresPhaseTimeouts:
    """Split one wall-clock budget into non-overlapping driver phases."""
    total_milliseconds = max(0, int(total_seconds * 1000))
    connect_milliseconds = int(total_milliseconds * 0.4)
    query_milliseconds = int(total_milliseconds * 0.4)
    safety_milliseconds = total_milliseconds - connect_milliseconds - query_milliseconds
    attempt_milliseconds = min(500, query_milliseconds // 4)
    if (
        connect_milliseconds < 1
        or query_milliseconds < 1
        or safety_milliseconds < 1
        or attempt_milliseconds < 1
    ):
        raise _LockDeadlineExceeded
    return _PostgresPhaseTimeouts(
        connect_milliseconds=connect_milliseconds,
        query_milliseconds=query_milliseconds,
        safety_milliseconds=safety_milliseconds,
        attempt_milliseconds=attempt_milliseconds,
    )


def _postgres_phase_deadlines(
    started: float,
    total_seconds: float,
    budget: _PostgresPhaseTimeouts,
) -> _PostgresPhaseDeadlines:
    connect = started + budget.connect_milliseconds / 1000
    query = connect + budget.query_milliseconds / 1000
    total = started + total_seconds
    if query + budget.safety_milliseconds / 1000 > total + 0.000_001:
        raise _LockDeadlineExceeded
    return _PostgresPhaseDeadlines(connect=connect, query=query, total=total)


def _lock_busy() -> HTTPException:
    return HTTPException(503, "idempotency_lock_busy")


def _acquire_process_lock(key: str, *, deadline: float) -> _ProcessLock:
    with _process_locks_guard:
        entry = _process_locks.get(key)
        if entry is None:
            entry = _ProcessLock(lock=threading.RLock())
            _process_locks[key] = entry
        entry.users += 1
    if not entry.lock.acquire(timeout=_remaining_seconds(deadline)):
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
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    path = _sqlite_lock_path(bind, key)
    process_key = f"sqlite-bucket:{path}" if path is not None and fcntl is not None else key
    entry = _acquire_process_lock(process_key, deadline=deadline)
    descriptor: int | None = None
    depths = getattr(_sqlite_thread_locks, "depths", None)
    if depths is None:
        depths = _sqlite_thread_locks.depths = {}
    nested = depths.get(process_key, 0) > 0
    try:
        if path is not None and fcntl is not None and not nested:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = _remaining_seconds(deadline)
                    if remaining <= 0:
                        raise HTTPException(503, "idempotency_lock_busy") from None
                    time.sleep(min(_SLEEP_SECONDS, remaining))
        depths[process_key] = depths.get(process_key, 0) + 1
        yield
    finally:
        depth = depths.get(process_key, 0)
        if depth <= 1:
            depths.pop(process_key, None)
        else:
            depths[process_key] = depth - 1
        if descriptor is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        _release_process_lock(process_key, entry)


def _postgres_lock_engine(bind: Any) -> _PostgresLockEngine:
    """Return a small lock-only pool which never waits for the request pool."""
    url = bind.url.render_as_string(hide_password=False)
    cache_key = hashlib.sha256(url.encode()).hexdigest()
    with _postgres_lock_engines_guard:
        state = _postgres_lock_engines.get(cache_key)
        if state is None:
            default_budget = _postgres_phase_timeouts(LOCK_WAIT_SECONDS)
            engine = create_engine(
                url,
                future=True,
                poolclass=QueuePool,
                pool_size=POSTGRES_LOCK_POOL_SIZE,
                max_overflow=0,
                pool_timeout=default_budget.connect_milliseconds / 1000,
                pool_pre_ping=False,
                pool_recycle=0,
                pool_use_lifo=True,
            )

            @event.listens_for(engine, "do_connect")
            def _bound_physical_connect(_dialect, _record, _cargs, cparams) -> None:
                deadline = getattr(_postgres_connect_deadline, "value", None)
                if deadline is None:
                    deadline = time.monotonic() + default_budget.connect_milliseconds / 1000
                remaining = _remaining_seconds(deadline)
                connect_seconds = int(remaining)
                if connect_seconds < 1:
                    raise _LockDeadlineExceeded
                attempt_milliseconds = int(
                    getattr(_postgres_connect_deadline, "attempt_milliseconds", None)
                    or default_budget.attempt_milliseconds
                )
                cparams["connect_timeout"] = connect_seconds
                cparams["tcp_user_timeout"] = attempt_milliseconds
                deadline_options = (
                    f"-c statement_timeout={attempt_milliseconds} "
                    f"-c lock_timeout={attempt_milliseconds}"
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


def _acquire_postgres_key(
    connection: Any,
    key: str,
    deadlines: _PostgresPhaseDeadlines,
    budget: _PostgresPhaseTimeouts,
) -> None:
    while True:
        phase_remaining = _remaining_seconds(deadlines.query)
        if phase_remaining * 1000 < budget.attempt_milliseconds:
            raise _lock_busy()
        try:
            acquired = connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                {"key": key},
            )
        except (DBAPIError, SQLAlchemyTimeoutError, _LockDeadlineExceeded):
            raise _lock_busy() from None
        remaining = _remaining_seconds(deadlines.query)
        if remaining <= 0:
            raise _lock_busy()
        if acquired:
            return
        next_attempt_wait = remaining - budget.attempt_milliseconds / 1000
        if next_attempt_wait <= 0:
            raise _lock_busy()
        time.sleep(min(_SLEEP_SECONDS, next_attempt_wait))


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
        started = time.monotonic()
        budget = _postgres_phase_timeouts(LOCK_WAIT_SECONDS)
        deadlines = _postgres_phase_deadlines(started, LOCK_WAIT_SECONDS, budget)
        state = _postgres_lock_engine(bind)
        active = getattr(_postgres_active_scope, "value", None)
        if active is not None and active[0] is state:
            _acquire_postgres_key(active[1], key, deadlines, budget)
            yield
            return
        previous_scope = active
        try:
            if _remaining_seconds(deadlines.connect) <= 0:
                raise _lock_busy()
            _postgres_connect_deadline.value = deadlines.connect
            _postgres_connect_deadline.attempt_milliseconds = budget.attempt_milliseconds
            try:
                try:
                    connection_context = state.engine.connect()
                except (DBAPIError, SQLAlchemyTimeoutError, _LockDeadlineExceeded):
                    raise _lock_busy() from None
                with connection_context as connection:
                    if _remaining_seconds(deadlines.connect) <= 0:
                        raise _lock_busy()
                    try:
                        transaction_context = connection.begin()
                    except (DBAPIError, SQLAlchemyTimeoutError, _LockDeadlineExceeded):
                        raise _lock_busy() from None
                    with transaction_context:
                        _acquire_postgres_key(connection, key, deadlines, budget)
                        if _remaining_seconds(deadlines.query) <= 0:
                            raise _lock_busy()
                        _postgres_active_scope.value = (state, connection)
                        try:
                            yield
                        finally:
                            _postgres_active_scope.value = previous_scope
            except _LockDeadlineExceeded:
                raise _lock_busy() from None
            finally:
                _postgres_connect_deadline.value = None
                _postgres_connect_deadline.attempt_milliseconds = None
        finally:
            _postgres_active_scope.value = previous_scope
        return
    if dialect == "sqlite":
        with _sqlite_file_lock(bind, key):
            yield
        return
    with _sqlite_file_lock(bind, key):
        yield
