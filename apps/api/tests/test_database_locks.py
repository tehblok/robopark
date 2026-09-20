import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
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
        release.wait(timeout=10)


def _acquire_sqlite_lock(database, key, attempting, acquired, blocked=None):
    original_flock = database_locks.fcntl.flock if database_locks.fcntl is not None else None

    if blocked is not None and original_flock is not None:

        def observed_flock(descriptor, operation):
            try:
                return original_flock(descriptor, operation)
            except BlockingIOError:
                blocked.set()
                raise

        database_locks.fcntl.flock = observed_flock
    attempting.set()
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

    lock_engine = database_locks._PostgresLockEngine(Engine(), threading.BoundedSemaphore(2))
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

    monkeypatch.setattr(
        database_locks,
        "_postgres_lock_engine",
        lambda _bind: database_locks._PostgresLockEngine(
            LockEngine(), threading.BoundedSemaphore(2)
        ),
    )
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


def test_postgresql_lock_caps_independent_connections_and_times_out_waiter(monkeypatch):
    """A third lock waiter times out without opening beyond the two-connection cap."""
    request_engine = create_engine(
        "sqlite://", poolclass=QueuePool, pool_size=1, max_overflow=0, future=True
    )
    active = 0
    maximum_active = 0
    active_lock = threading.Lock()
    two_opened = threading.Event()
    release_holders = threading.Event()

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Connection:
        def __enter__(self):
            nonlocal active, maximum_active
            with active_lock:
                active += 1
                maximum_active = max(maximum_active, active)
                if active == 2:
                    two_opened.set()
            return self

        def __exit__(self, *_args):
            nonlocal active
            with active_lock:
                active -= 1

        def begin(self):
            return Transaction()

        def scalar(self, _statement, _parameters):
            return True

    class LockEngine:
        def connect(self):
            return Connection()

    class RequestBind:
        dialect = SimpleNamespace(name="postgresql")

        def connect(self):
            return request_engine.connect()

    class RequestSession:
        def get_bind(self):
            return RequestBind()

    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", 0.1)
    state = database_locks._PostgresLockEngine(LockEngine(), threading.BoundedSemaphore(2))
    monkeypatch.setattr(database_locks, "_postgres_lock_engine", lambda _bind: state)

    def hold(key):
        with database_idempotency_lock(RequestSession(), key):
            assert release_holders.wait(timeout=2)

    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            first = executor.submit(hold, "first")
            second = executor.submit(hold, "second")
            assert two_opened.wait(timeout=1)
            started = time.monotonic()
            third = executor.submit(
                lambda: database_idempotency_lock(RequestSession(), "third").__enter__()
            )
            with pytest.raises(HTTPException, match="idempotency_lock_busy"):
                third.result(timeout=1)
            assert time.monotonic() - started < 0.5
            with RequestBind().connect() as request_connection:
                assert request_connection.scalar(text("SELECT 1")) == 1
            release_holders.set()
            first.result(timeout=1)
            second.result(timeout=1)
    finally:
        release_holders.set()
        request_engine.dispose()

    assert maximum_active == 2


@pytest.mark.parametrize("slow_phase", ["connect", "scalar"])
def test_postgresql_lock_rejects_success_returned_after_deadline(monkeypatch, slow_phase):
    """A late connect or scalar result must never enter the protected workflow."""
    scalar_called = False

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def begin(self):
            return Transaction()

        def scalar(self, _statement, _parameters):
            nonlocal scalar_called
            scalar_called = True
            if slow_phase == "scalar":
                time.sleep(0.2)
            return True

    class LockEngine:
        def connect(self):
            if slow_phase == "connect":
                time.sleep(0.2)
            return Connection()

    class RequestSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", 0.05)
    state = database_locks._PostgresLockEngine(LockEngine(), threading.BoundedSemaphore(1))
    monkeypatch.setattr(database_locks, "_postgres_lock_engine", lambda _bind: state)

    entered = False
    with (
        pytest.raises(HTTPException, match="idempotency_lock_busy"),
        database_idempotency_lock(RequestSession(), "late-success"),
    ):
        entered = True

    assert entered is False
    assert scalar_called is (slow_phase == "scalar")


def test_postgresql_phase_timeouts_fit_inside_total_deadline():
    budget = database_locks._postgres_phase_timeouts(5.0)
    deadlines = database_locks._postgres_phase_deadlines(10.0, 5.0, budget)

    assert budget.connect_seconds == 2
    assert budget.query_milliseconds == 2000
    assert budget.safety_milliseconds == 1000
    assert budget.attempt_milliseconds == 500
    assert (
        budget.connect_seconds * 1000 + budget.query_milliseconds + budget.safety_milliseconds
        <= 5000
    )
    assert deadlines.connect == 12.0
    assert deadlines.query == 14.0
    assert deadlines.total == 15.0
    assert deadlines.total - deadlines.query == 1.0


def test_postgresql_driver_timeout_is_retryable_and_releases_resources(monkeypatch):
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
            raise OperationalError("SELECT", {}, TimeoutError("driver timeout"))

    class LockEngine:
        def connect(self):
            return Connection()

    class RequestSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    state = database_locks._PostgresLockEngine(LockEngine(), threading.BoundedSemaphore(1))
    monkeypatch.setattr(database_locks, "_postgres_lock_engine", lambda _bind: state)

    with (
        pytest.raises(HTTPException, match="idempotency_lock_busy"),
        database_idempotency_lock(RequestSession(), "driver-timeout"),
    ):
        pytest.fail("driver timeout must not enter the protected workflow")

    assert events == [
        "connection-open",
        "transaction-open",
        "try-lock",
        "transaction-closed",
        "connection-closed",
    ]
    assert state.slots.acquire(blocking=False)
    state.slots.release()


def test_postgresql_phase_timeout_bounds_elapsed_and_preserves_body_errors(monkeypatch):
    total_seconds = 2.5
    budget = database_locks._postgres_phase_timeouts(total_seconds)
    fail_acquisition = True

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def begin(self):
            return Transaction()

        def scalar(self, _statement, _parameters):
            if fail_acquisition:
                time.sleep(budget.attempt_milliseconds / 1000)
                raise OperationalError("SELECT", {}, TimeoutError("statement timeout"))
            return True

    class LockEngine:
        def connect(self):
            return Connection()

    class RequestSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", total_seconds)
    state = database_locks._PostgresLockEngine(LockEngine(), threading.BoundedSemaphore(1))
    monkeypatch.setattr(database_locks, "_postgres_lock_engine", lambda _bind: state)

    started = time.monotonic()
    with (
        pytest.raises(HTTPException, match="idempotency_lock_busy"),
        database_idempotency_lock(RequestSession(), "bounded-driver-timeout"),
    ):
        pytest.fail("driver timeout must not enter the protected workflow")
    assert time.monotonic() - started < total_seconds

    fail_acquisition = False
    body_error = OperationalError("body", {}, RuntimeError("workflow failure"))
    with (
        pytest.raises(OperationalError) as captured,
        database_idempotency_lock(RequestSession(), "body-error"),
    ):
        raise body_error
    assert captured.value is body_error


def test_postgresql_late_retry_never_starts_past_query_attempt_budget(monkeypatch):
    slow_retry_started = False
    attempts = 0

    class Transaction:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def begin(self):
            return Transaction()

        def scalar(self, _statement, _parameters):
            nonlocal attempts, slow_retry_started
            attempts += 1
            if attempts <= 6:
                time.sleep(0.02)
                return False
            slow_retry_started = True
            time.sleep(0.2)
            return True

    class LockEngine:
        def connect(self):
            return Connection()

    class RequestSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    total_seconds = 0.3
    monkeypatch.setattr(database_locks, "LOCK_WAIT_SECONDS", total_seconds)
    state = database_locks._PostgresLockEngine(LockEngine(), threading.BoundedSemaphore(1))
    monkeypatch.setattr(database_locks, "_postgres_lock_engine", lambda _bind: state)

    started = time.monotonic()
    with (
        pytest.raises(HTTPException, match="idempotency_lock_busy"),
        database_idempotency_lock(RequestSession(), "late-retry"),
    ):
        pytest.fail("late retry must not enter the protected workflow")

    assert time.monotonic() - started <= total_seconds + 0.05
    assert 1 <= attempts <= 6
    assert slow_retry_started is False


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
    same_attempting = context.Event()
    different_attempting = context.Event()
    same_blocked = context.Event()
    holder = context.Process(
        target=_hold_sqlite_lock,
        args=(database, first_key, holder_entered, release_holder),
    )
    same = context.Process(
        target=_acquire_sqlite_lock,
        args=(database, first_key, same_attempting, same_acquired, same_blocked),
    )
    different = context.Process(
        target=_acquire_sqlite_lock,
        args=(database, other_key, different_attempting, different_acquired),
    )
    holder.start()
    try:
        assert holder_entered.wait(timeout=5)
        same.start()
        different.start()
        assert same_attempting.wait(timeout=5)
        assert different_attempting.wait(timeout=5)
        assert same_blocked.wait(timeout=5)
        assert different_acquired.wait(timeout=5)
        assert not same_acquired.is_set()
        release_holder.set()
        assert same_acquired.wait(timeout=5)
    finally:
        release_holder.set()
        for process in (holder, same, different):
            process.join(timeout=5)
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
