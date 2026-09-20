import multiprocessing
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

from robopark_api.services import database_locks
from robopark_api.services.database_locks import database_idempotency_lock


class _SqliteBind:
    dialect = SimpleNamespace(name="sqlite")

    def __init__(self, database):
        self.url = SimpleNamespace(database=str(database))


class _SqliteDb:
    def __init__(self, database):
        self.bind = _SqliteBind(database)

    def get_bind(self):
        return self.bind


def _hold_sqlite_lock(database, key, entered, release):
    with database_idempotency_lock(_SqliteDb(database), key):
        entered.set()
        release.wait(timeout=2)


def _acquire_sqlite_lock(database, key, acquired):
    with database_idempotency_lock(_SqliteDb(database), key):
        acquired.set()


def test_postgresql_idempotency_lock_keeps_dedicated_transaction_through_request_rebind(
    monkeypatch,
):
    """The advisory lock must not live on a RequestSession connection."""
    events: list[str] = []

    class Transaction:
        def __enter__(self):
            events.append("transaction-open")
            return self

        def __exit__(self, *_args):
            events.append("transaction-closed")

    class Connection:
        def __enter__(self):
            events.append("connection-open")
            return self

        def __exit__(self, *_args):
            events.append("connection-closed")

        def begin(self):
            return Transaction()

        def scalar(self, _statement, _parameters):
            events.append("try-lock")
            return True

    class Engine:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return Connection()

    class RequestSession:
        def __init__(self):
            self.releases = 0

        def get_bind(self):
            return Engine()

        def release_connection(self):
            self.releases += 1
            events.append("request-rebound")

    lock_engine = Engine()
    request = RequestSession()
    # The request bind intentionally has no connect method: the lock must use
    # the independent lock-only engine.
    monkeypatch.setattr(database_locks, "_postgres_lock_engine", lambda _bind: lock_engine)
    with database_idempotency_lock(request, "receipt-key"):
        request.release_connection()
        assert events == ["connection-open", "transaction-open", "try-lock", "request-rebound"]

    assert request.releases == 1
    assert events[-2:] == ["transaction-closed", "connection-closed"]


def test_postgresql_lock_does_not_wait_for_prechecked_request_queue_pool(monkeypatch):
    """A saturated request pool must not participate in idempotency locking."""
    request_engine = create_engine(
        "sqlite://", poolclass=QueuePool, pool_size=1, max_overflow=0, future=True
    )
    checked_out = request_engine.connect()
    events: list[str] = []

    class Transaction:
        def __enter__(self):
            events.append("transaction-open")
            return self

        def __exit__(self, *_args):
            events.append("transaction-closed")

    class LockConnection:
        def __enter__(self):
            events.append("lock-connection-open")
            return self

        def __exit__(self, *_args):
            events.append("lock-connection-closed")

        def begin(self):
            return Transaction()

        def scalar(self, _statement, _parameters):
            return True

    class LockEngine:
        def connect(self):
            return LockConnection()

    class RequestBind:
        dialect = SimpleNamespace(name="postgresql")
        pool = request_engine.pool

        def connect(self):
            raise AssertionError("lock must not borrow a request-pool connection")

    class RequestSession:
        def get_bind(self):
            return RequestBind()

    monkeypatch.setattr(database_locks, "_postgres_lock_engine", lambda _bind: LockEngine())
    try:
        with database_idempotency_lock(RequestSession(), "pool-isolation"):
            assert request_engine.pool.checkedout() == 1
    finally:
        checked_out.close()
        request_engine.dispose()

    assert events == [
        "lock-connection-open",
        "transaction-open",
        "transaction-closed",
        "lock-connection-closed",
    ]


@pytest.mark.skipif(database_locks.fcntl is None, reason="fcntl flock requires Unix")
def test_sqlite_bucket_locks_exclude_same_bucket_and_allow_different_bucket(tmp_path):
    """Child processes prove host-worker exclusion without a global file lock."""
    database = tmp_path / "robopark.db"
    first_key = "same-key"
    first_path = database_locks._sqlite_lock_path(_SqliteBind(database), first_key)
    assert first_path is not None
    other_key = next(
        candidate
        for candidate in (f"different-{index}" for index in range(256))
        if database_locks._sqlite_lock_path(_SqliteBind(database), candidate) != first_path
    )
    context = multiprocessing.get_context("spawn")
    holder_entered = context.Event()
    release_holder = context.Event()
    same_acquired = context.Event()
    different_acquired = context.Event()
    holder = context.Process(
        target=_hold_sqlite_lock,
        args=(database, first_key, holder_entered, release_holder),
    )
    same = context.Process(target=_acquire_sqlite_lock, args=(database, first_key, same_acquired))
    different = context.Process(
        target=_acquire_sqlite_lock, args=(database, other_key, different_acquired)
    )
    holder.start()
    try:
        assert holder_entered.wait(timeout=2)
        same.start()
        different.start()
        assert different_acquired.wait(timeout=2)
        assert not same_acquired.wait(timeout=0.15)
        release_holder.set()
        assert same_acquired.wait(timeout=2)
    finally:
        release_holder.set()
        for process in (holder, same, different):
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)

    assert all(process.exitcode == 0 for process in (holder, same, different))


def test_sqlite_bucket_files_and_process_lock_registry_are_bounded(tmp_path):
    database = tmp_path / "robopark.db"
    db = _SqliteDb(database)
    for index in range(database_locks.SQLITE_LOCK_BUCKETS * 3):
        with database_idempotency_lock(db, f"unique-{index}"):
            pass

    root = tmp_path / ".robopark-idempotency-locks"
    assert len(list(root.glob("bucket-*.lock"))) <= database_locks.SQLITE_LOCK_BUCKETS
    assert database_locks._process_locks == {}
