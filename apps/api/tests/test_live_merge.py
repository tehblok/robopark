"""Live-merge store: short flock, result after HTTP, no lock during loader."""

from __future__ import annotations

import fcntl
import threading
import time

import pytest

from robopark_api.services.live_merge import LiveMergeStore, LiveMergeTimeout
from robopark_api.services.response_cache import ResponseCache


def test_lock_is_not_held_during_loader(tmp_path):
    store = LiveMergeStore(tmp_path)
    saw_unlocked = threading.Event()

    def loader() -> int:
        lock_path = store.lock_path("ns", "k")
        with lock_path.open("a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        saw_unlocked.set()
        return 7

    assert store.merge_load("ns", "k", 60, loader) == 7
    assert saw_unlocked.is_set()


def test_overlapping_threads_share_one_loader(tmp_path):
    store = LiveMergeStore(tmp_path)
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def loader() -> dict:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2.0)
        return {"n": calls}

    results: list[dict] = []

    def worker() -> None:
        results.append(store.merge_load("ns", "shared", 60, loader))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    assert started.wait(timeout=2.0)
    release.set()
    for t in threads:
        t.join(timeout=2.0)

    assert results == [{"n": 1}] * 4
    assert calls == 1


def test_different_keys_do_not_share_a_lock(tmp_path):
    store = LiveMergeStore(tmp_path)
    calls: dict[str, int] = {"a": 0, "b": 0}
    started = {k: threading.Event() for k in ("a", "b")}
    release = threading.Event()

    def loader_for(key: str):
        def loader() -> str:
            calls[key] += 1
            started[key].set()
            assert release.wait(timeout=2.0)
            return key

        return loader

    results: list[str] = []

    def worker(key: str) -> None:
        results.append(store.merge_load("ns", key, 60, loader_for(key)))

    threads = [
        threading.Thread(target=worker, args=("a",)),
        threading.Thread(target=worker, args=("b",)),
    ]
    for t in threads:
        t.start()
    assert started["a"].wait(timeout=2.0)
    assert started["b"].wait(timeout=2.0)
    release.set()
    for t in threads:
        t.join(timeout=2.0)

    assert sorted(results) == ["a", "b"]
    assert calls == {"a": 1, "b": 1}


def test_waiter_times_out_on_stuck_live_leader(tmp_path):
    store = LiveMergeStore(tmp_path, waiter_timeout=0.25)
    store._write_inflight("ns", "stuck")
    t0 = time.monotonic()
    with pytest.raises(LiveMergeTimeout):
        store.merge_load("ns", "stuck", 60, lambda: 1)
    assert time.monotonic() - t0 < 1.5
    assert store._read_inflight("ns", "stuck") is not None


def test_second_cache_reads_blob_without_loader(tmp_path):
    store = LiveMergeStore(tmp_path)
    first: ResponseCache[dict] = ResponseCache(60, name="blob", shared=store)
    second: ResponseCache[dict] = ResponseCache(60, name="blob", shared=store)
    calls = 0

    def loader() -> dict:
        nonlocal calls
        calls += 1
        return {"n": calls}

    assert first.get_or_load("k", loader) == {"n": 1}
    assert second.get_or_load("k", lambda: {"n": 99}) == {"n": 1}
    assert calls == 1


def test_invalidate_drops_blob_for_other_cache(tmp_path):
    store = LiveMergeStore(tmp_path)
    first: ResponseCache[int] = ResponseCache(60, name="inv", shared=store)
    second: ResponseCache[int] = ResponseCache(60, name="inv", shared=store)
    assert first.get_or_load("k", lambda: 1) == 1
    first.invalidate("k")
    assert second.get_or_load("k", lambda: 2) == 2


def test_dead_pid_inflight_is_ignored(tmp_path):
    store = LiveMergeStore(tmp_path)
    store._atomic_write(
        store.inflight_path("ns", "k"),
        {"pid": 2_000_000_000, "started_at": time.time()},
    )
    assert store._read_inflight("ns", "k") is None
    assert store.merge_load("ns", "k", 60, lambda: 42) == 42


def test_failed_refresh_keeps_last_good_blob(tmp_path):
    store = LiveMergeStore(tmp_path)
    assert store.merge_load("ns", "k", 0.05, lambda: {"n": 1}) == {"n": 1}
    time.sleep(0.06)
    calls = 0

    def boom() -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("429")

    assert store.merge_load("ns", "k", 0.05, boom) == {"n": 1}
    assert calls == 1


def test_failure_without_prior_does_not_refetch(tmp_path):
    store = LiveMergeStore(tmp_path, waiter_timeout=2.0)
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def boom() -> int:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2.0)
        raise RuntimeError("429")

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            store.merge_load("ns", "k", 60, boom)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    assert started.wait(timeout=2.0)
    release.set()
    for t in threads:
        t.join(timeout=2.0)

    assert calls == 1
    assert len(errors) == 3
