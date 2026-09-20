"""Bounded cross-worker locks for idempotent local workflows."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

try:  # Robopark is deployed on Linux; keep unit tests portable.
    import fcntl
except ImportError:  # pragma: no cover - non-Unix development fallback
    fcntl = None


LOCK_WAIT_SECONDS = 5.0
_SLEEP_SECONDS = 0.02


@dataclass
class _ProcessLock:
    lock: threading.Lock
    users: int = 0


_process_locks: dict[str, _ProcessLock] = {}
_process_locks_guard = threading.Lock()


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


def _sqlite_lock_path(bind: Any) -> Path | None:
    database = getattr(getattr(bind, "url", None), "database", None)
    if not database or database == ":memory:":
        return None
    root = Path(database).parent / ".robopark-idempotency-locks"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # One stable lock file avoids leaving an unbounded lock-file collection on
    # constrained hosts. The in-process keyed lock keeps unrelated actions
    # concurrent inside one worker; `flock` provides the conservative
    # cross-process SQLite boundary.
    return root / "sqlite-idempotency.lock"


@contextmanager
def _sqlite_file_lock(bind: Any, key: str) -> Iterator[None]:
    """Use a prunable process lock plus `flock` for separate SQLite workers."""
    entry = _acquire_process_lock(key)
    descriptor: int | None = None
    try:
        path = _sqlite_lock_path(bind)
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


@contextmanager
def database_idempotency_lock(db: Any, key: str) -> Iterator[None]:
    """Hold a bounded idempotency lock across commits and request re-binding.

    PostgreSQL uses a dedicated connection and a transaction-scoped advisory
    lock. It never relies on the request session, which may release and later
    replace its physical connection around upstream waits. SQLite keeps a
    keyed in-process lock and a non-blocking `flock` sidecar for coordination
    across its separate host worker processes.
    """
    bind = db.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        with bind.connect() as connection, connection.begin():
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while not connection.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {"key": key}
            ):
                if time.monotonic() >= deadline:
                    raise HTTPException(503, "idempotency_lock_busy")
                time.sleep(_SLEEP_SECONDS)
            yield
        return
    if dialect == "sqlite":
        with _sqlite_file_lock(bind, key):
            yield
        return
    with _sqlite_file_lock(bind, key):
        yield
