"""Live-merge store: short flock, result after HTTP, no lock during loader."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
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


def test_invalidate_during_flight_cannot_restore_stale_value(tmp_path):
    store = LiveMergeStore(tmp_path, waiter_timeout=2.0)
    old_cache: ResponseCache[str] = ResponseCache(60, name="race", shared=store)
    new_cache: ResponseCache[str] = ResponseCache(60, name="race", shared=store)
    observer: ResponseCache[str] = ResponseCache(60, name="race", shared=store)
    old_started = threading.Event()
    release_old = threading.Event()
    old_result: list[str] = []

    def old_loader() -> str:
        old_started.set()
        assert release_old.wait(timeout=2.0)
        return "stale"

    thread = threading.Thread(
        target=lambda: old_result.append(old_cache.get_or_load("k", old_loader))
    )
    thread.start()
    assert old_started.wait(timeout=2.0)

    new_cache.invalidate("k")
    assert new_cache.get_or_load("k", lambda: "fresh") == "fresh"
    release_old.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert old_result == ["fresh"]
    assert observer.get_or_load("k", lambda: "unexpected") == "fresh"
    assert old_cache.get_or_load("k", lambda: "unexpected") == "fresh"


def test_clear_namespace_waits_out_a_finishing_leader(tmp_path, monkeypatch):
    store = LiveMergeStore(tmp_path, waiter_timeout=2.0)
    old_cache: ResponseCache[str] = ResponseCache(60, name="clear-race", shared=store)
    clearing_cache: ResponseCache[str] = ResponseCache(60, name="clear-race", shared=store)
    observer: ResponseCache[str] = ResponseCache(60, name="clear-race", shared=store)
    write_started = threading.Event()
    release_write = threading.Event()
    clear_done = threading.Event()
    original_write_result = store._write_result

    def paused_write_result(namespace: str, key: str, payload: object) -> None:
        write_started.set()
        assert release_write.wait(timeout=2.0)
        original_write_result(namespace, key, payload)

    monkeypatch.setattr(store, "_write_result", paused_write_result)
    leader = threading.Thread(target=lambda: old_cache.get_or_load("k", lambda: "stale"))
    leader.start()
    assert write_started.wait(timeout=2.0)

    clearer = threading.Thread(target=lambda: (clearing_cache.clear(), clear_done.set()))
    clearer.start()
    assert not clear_done.wait(timeout=0.1)
    release_write.set()
    leader.join(timeout=2.0)
    clearer.join(timeout=2.0)

    assert not leader.is_alive()
    assert not clearer.is_alive()
    assert observer.get_or_load("k", lambda: "fresh") == "fresh"
    assert old_cache.get_or_load("k", lambda: "unexpected") == "fresh"


def test_repeated_invalidation_is_bounded_without_recursion(tmp_path):
    store = LiveMergeStore(tmp_path, waiter_timeout=0.05)
    calls = 0

    def invalidated_loader() -> int:
        nonlocal calls
        calls += 1
        store.invalidate("ns", "hot")
        return calls

    with pytest.raises(LiveMergeTimeout):
        store.merge_load("ns", "hot", 60, invalidated_loader)

    assert calls < 100


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


def test_failed_refresh_rejects_blob_aged_sixty_seconds(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("robopark_api.services.live_merge.time.time", lambda: now[0])
    store = LiveMergeStore(tmp_path)
    assert store.merge_load("ns", "k", 3, lambda: {"n": 1}) == {"n": 1}

    def boom() -> dict:
        raise RuntimeError("unavailable")

    now[0] = 104.0
    assert store.merge_load("ns", "k", 3, boom) == {"n": 1}
    now[0] = 160.0
    with pytest.raises(RuntimeError, match="unavailable"):
        store.merge_load("ns", "k", 3, boom)


@pytest.mark.parametrize("control_flow", [KeyboardInterrupt(), asyncio.CancelledError()])
def test_stale_blob_does_not_swallow_control_flow(tmp_path, control_flow, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("robopark_api.services.live_merge.time.time", lambda: now[0])
    store = LiveMergeStore(tmp_path)
    assert store.merge_load("ns", "k", 3, lambda: {"n": 1}) == {"n": 1}
    now[0] = 104.0

    def interrupt() -> dict:
        raise control_flow

    with pytest.raises(type(control_flow)):
        store.merge_load("ns", "k", 3, interrupt)

    assert not store.inflight_path("ns", "k").exists()


def test_prune_removes_old_merge_files_but_preserves_live_inflight(tmp_path):
    now = 10_000.0
    store = LiveMergeStore(tmp_path)
    folder = store.namespace_dir("ns")
    folder.mkdir(parents=True)
    old = {
        "result": folder / "old.json",
        "error": folder / "old.error",
        "lock": folder / "old.lock",
        "tmp": folder / "old.tmp",
        "dead": folder / "dead.inflight",
    }
    old["result"].write_text('{"ok":true}', encoding="utf-8")
    old["error"].write_text('{"ok":false}', encoding="utf-8")
    old["lock"].touch()
    old["tmp"].touch()
    old["dead"].write_text(
        json.dumps({"pid": 2_000_000_000, "started_at": now - 100}), encoding="utf-8"
    )
    live = folder / "live.inflight"
    live.write_text(json.dumps({"pid": os.getpid(), "started_at": now - 100}), encoding="utf-8")
    fresh = folder / "fresh.json"
    fresh.write_text('{"ok":true}', encoding="utf-8")
    for path in (*old.values(), live):
        os.utime(path, (now - 100, now - 100))
    os.utime(old["tmp"], (now - 3601, now - 3601))
    os.utime(fresh, (now - 10, now - 10))

    removed = store.prune(now=now, blob_max_age_seconds=60, lock_max_age_seconds=60)

    assert removed == len(old)
    assert all(not path.exists() for path in old.values())
    assert live.exists()
    assert fresh.exists()


def test_prune_tolerates_one_file_disappearing_or_failing(tmp_path, monkeypatch):
    now = 10_000.0
    store = LiveMergeStore(tmp_path)
    folder = store.namespace_dir("ns")
    folder.mkdir(parents=True)
    failing = folder / "failing.json"
    removable = folder / "removable.error"
    failing.touch()
    removable.touch()
    os.utime(failing, (now - 100, now - 100))
    os.utime(removable, (now - 100, now - 100))
    path_type = type(failing)
    original_unlink = path_type.unlink

    def unlink(path, *args, **kwargs):
        if path == failing:
            raise OSError("raced")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "unlink", unlink)

    assert store.prune(now=now, blob_max_age_seconds=60) == 1
    assert failing.exists()
    assert not removable.exists()


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


def test_targeted_payload_invalidation_keeps_unrelated_shared_projection(tmp_path):
    store = LiveMergeStore(tmp_path)
    store.merge_load("tracker.issues", "one", 60, lambda: [{"key": "SD-1"}])
    store.merge_load("tracker.issues", "two", 60, lambda: [{"key": "SD-2"}])

    assert store.invalidate_payload_member("tracker.issues", "SD-1") == 1

    assert store.try_fresh("tracker.issues", "one", 60)[0] is False
    assert store.try_fresh("tracker.issues", "two", 60) == (True, [{"key": "SD-2"}])
