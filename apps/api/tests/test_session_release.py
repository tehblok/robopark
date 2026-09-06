"""U1: waiters must not pin pooled SQLite connections during a live-merge wait."""

from __future__ import annotations

import threading
import time
from contextlib import ExitStack

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from robopark_api.db import RequestSession, bind_request_session, reset_request_session
from robopark_api.services.live_merge import LiveMergeStore
from robopark_api.services.response_cache import ResponseCache


def test_sqlite_request_stages_do_not_queue_behind_checked_out_connections(tmp_path):
    from robopark_api.db import engine as configured_engine

    # Copy only the production pool policy into a fresh temporary database.
    # Never connect to configured_engine (it may point at a developer's DB).
    pool_class = type(configured_engine.pool)
    options = {"pool_timeout": 0.05} if issubclass(pool_class, QueuePool) else {}
    engine = create_engine(
        f"sqlite:///{tmp_path / 'request-stages.db'}",
        poolclass=pool_class,
        **options,
    )
    try:
        with ExitStack() as requests:
            # Completed auth dependencies retain their read connection until
            # a later endpoint/cleanup stage is scheduled. A new auth must not
            # consume every worker thread waiting behind that finite pool.
            for _ in range(41):
                conn = requests.enter_context(engine.connect())
                assert conn.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()


def test_shared_merge_waiter_releases_connection_before_waiting(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'shared-pool.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.2,
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine)
    waiting = threading.Event()

    class ObservedStore(LiveMergeStore):
        def _read_inflight(self, namespace, key):
            result = super()._read_inflight(namespace, key)
            if result is not None:
                waiting.set()
            return result

    store = ObservedStore(tmp_path / "merge", waiter_timeout=2)
    store._write_inflight("emergency.robot", "shared")
    results = []
    errors = []

    def worker():
        wrapper = RequestSession(factory)
        token = bind_request_session(wrapper)
        try:
            wrapper.execute(text("SELECT 1"))
            results.append(store.merge_load("emergency.robot", "shared", 60, lambda: 99))
        except BaseException as exc:
            errors.append(exc)
        finally:
            wrapper.close()
            reset_request_session(token)

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert waiting.wait(timeout=1)
        # A different request must be able to use the only pool connection
        # while another process owns the slow upstream flight.
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 42")) == 42
    finally:
        store._write_result("emergency.robot", "shared", 7)
        thread.join(timeout=3)
        engine.dispose()
    assert not thread.is_alive()
    assert errors == []
    assert results == [7]


def test_waiters_do_not_hold_pool_connections_during_flight(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pool.db'}",
        future=True,
        pool_size=5,
        max_overflow=0,
        pool_timeout=1.0,
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, future=True, autoflush=False, autocommit=False)
    cache: ResponseCache[int] = ResponseCache(60, name="pool")
    started = threading.Event()
    release = threading.Event()
    checked_during_wait = threading.Event()
    checked_out: list[int] = []

    def loader() -> int:
        started.set()
        assert checked_during_wait.wait(timeout=2.0)
        assert release.wait(timeout=2.0)
        return 7

    errors: list[BaseException] = []
    results: list[int] = []

    def worker() -> None:
        wrapper = RequestSession(factory)
        token = bind_request_session(wrapper)
        try:
            wrapper.execute(text("SELECT 1"))
            results.append(cache.get_or_load("shared", loader))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            wrapper.close()
            reset_request_session(token)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    assert started.wait(timeout=2.0)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        n = engine.pool.checkedout()
        if n == 0:
            break
        time.sleep(0.01)
    checked_out.append(engine.pool.checkedout())
    checked_during_wait.set()
    release.set()
    for t in threads:
        t.join(timeout=2.0)

    assert errors == []
    assert results == [7] * 8
    # Leader and waiters release the pool connection before the upstream wait.
    assert checked_out[0] == 0
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
