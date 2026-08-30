"""U1: waiters must not pin pooled SQLite connections during a live-merge wait."""

from __future__ import annotations

import threading
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from robopark_api.db import RequestSession, bind_request_session, reset_request_session
from robopark_api.services.response_cache import ResponseCache


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
