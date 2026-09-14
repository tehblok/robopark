import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from robopark_api.services.response_cache import ResponseCache


def test_get_or_load_caches_first_result():
    cache: ResponseCache[dict] = ResponseCache(60, name="unit")
    calls = 0

    def loader() -> dict:
        nonlocal calls
        calls += 1
        return {"n": calls}

    first = cache.get_or_load("k", loader)
    second = cache.get_or_load("k", loader)

    assert first == {"n": 1}
    assert second == {"n": 1}
    assert calls == 1


def test_expired_entry_triggers_refetch():
    cache: ResponseCache[int] = ResponseCache(0.01, name="unit")

    cache.get_or_load("k", lambda: 1)
    time.sleep(0.02)
    assert cache.get_or_load("k", lambda: 2) == 2


def test_invalidate_forces_refetch():
    cache: ResponseCache[int] = ResponseCache(60, name="unit")
    cache.get_or_load("a", lambda: 1)
    cache.get_or_load("b", lambda: 10)

    cache.invalidate("a")
    assert cache.get_or_load("a", lambda: 2) == 2
    assert cache.get_or_load("b", lambda: 20) == 10


def test_invalidate_prefix_drops_matching_keys():
    cache: ResponseCache[int] = ResponseCache(60, name="unit")
    cache.get_or_load("issues:one", lambda: 1)
    cache.get_or_load("issues:two", lambda: 2)
    cache.get_or_load("other", lambda: 9)

    cache.invalidate_prefix("issues:")

    assert cache.get_or_load("issues:one", lambda: 100) == 100
    assert cache.get_or_load("issues:two", lambda: 200) == 200
    assert cache.get_or_load("other", lambda: 999) == 9


def test_single_flight_dedupes_concurrent_misses():
    cache: ResponseCache[int] = ResponseCache(60, name="unit")
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def loader() -> int:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=1.0)
        return 42

    results: list[int] = []

    def worker() -> None:
        results.append(cache.get_or_load("shared", loader))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    started.wait(timeout=1.0)
    release.set()
    for t in threads:
        t.join(timeout=1.0)

    assert results == [42, 42, 42, 42]
    assert calls == 1


def test_loader_exception_propagates_to_all_waiters():
    cache: ResponseCache[int] = ResponseCache(60, name="unit")

    def loader() -> int:
        raise RuntimeError("boom")

    results: list[BaseException] = []

    def worker() -> None:
        try:
            cache.get_or_load("k", loader)
        except BaseException as exc:  # noqa: BLE001
            results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=1.0)

    assert len(results) == 3
    assert all(isinstance(exc, RuntimeError) for exc in results)


def test_ttl_must_be_positive():
    with pytest.raises(ValueError):
        ResponseCache(0, name="unit")


def test_lru_evicts_oldest_entry_and_refreshes_hits():
    cache: ResponseCache[object] = ResponseCache(60, name="bounded", max_entries=2, shared=False)
    first = object()
    second = object()
    third = object()

    cache.get_or_load("first", lambda: first)
    cache.get_or_load("second", lambda: second)
    assert cache.get_or_load("first", lambda: object()) is first
    cache.get_or_load("third", lambda: third)

    assert cache.peek("first") is first
    assert cache.peek("second") is None
    assert cache.peek("third") is third


def test_expired_entries_are_pruned_when_another_key_is_loaded(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("robopark_api.services.response_cache.time.monotonic", lambda: now[0])
    cache: ResponseCache[int] = ResponseCache(1, name="expiry", max_entries=3, shared=False)
    cache.get_or_load("old", lambda: 1)

    now[0] = 71.0
    cache.get_or_load("new", lambda: 2)

    assert list(cache._store) == ["new"]


def test_default_cache_bound_is_finite():
    cache: ResponseCache[int] = ResponseCache(60, name="default-bound", shared=False)
    for index in range(1025):
        cache.get_or_load(str(index), lambda index=index: index)

    assert len(cache._store) == 1024
    assert cache.peek("0") is None


def test_max_entries_must_be_positive():
    with pytest.raises(ValueError, match="max_entries"):
        ResponseCache(60, name="unit", max_entries=0)


def test_two_hundred_same_key_callers_share_one_object_and_loader():
    callers = 200
    cache: ResponseCache[object] = ResponseCache(60, name="load", shared=False)
    ready = threading.Barrier(callers + 1)
    loader_started = threading.Event()
    release_loader = threading.Event()
    value = object()
    calls = 0

    def loader() -> object:
        nonlocal calls
        calls += 1
        loader_started.set()
        assert release_loader.wait(timeout=5)
        return value

    def worker() -> object:
        ready.wait(timeout=5)
        return cache.get_or_load("shared", loader)

    with ThreadPoolExecutor(max_workers=callers) as executor:
        futures = [executor.submit(worker) for _ in range(callers)]
        ready.wait(timeout=5)
        assert loader_started.wait(timeout=5)
        release_loader.set()
        results = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(result is value for result in results)
    assert cache._flights == {}


def test_loader_failure_returns_last_good_within_ttl():
    cache: ResponseCache[int] = ResponseCache(0.05, name="last-good")
    assert cache.get_or_load("k", lambda: 1) == 1
    time.sleep(0.06)
    calls = 0

    def boom() -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("429")

    assert cache.get_or_load("k", boom) == 1
    assert calls == 1


def test_loader_failure_rejects_last_good_after_sixty_seconds(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("robopark_api.services.response_cache.time.monotonic", lambda: now[0])
    cache: ResponseCache[int] = ResponseCache(3, name="last-good", shared=False)
    assert cache.get_or_load("k", lambda: 1) == 1

    def boom() -> int:
        raise RuntimeError("unavailable")

    now[0] = 104.0
    assert cache.get_or_load("k", boom) == 1
    now[0] = 160.0
    with pytest.raises(RuntimeError, match="unavailable"):
        cache.get_or_load("k", boom)


@pytest.mark.parametrize("failure", [RuntimeError("boom"), KeyboardInterrupt()])
def test_completed_flight_does_not_retain_errors_or_payloads(failure):
    cache: ResponseCache[int] = ResponseCache(60, name="flight-cleanup", shared=False)

    def fail() -> int:
        raise failure

    with pytest.raises(type(failure)):
        cache.get_or_load("k", fail)

    assert cache._flights == {}


def test_overlapping_failure_without_prior_is_one_shared_error():
    cache: ResponseCache[int] = ResponseCache(60, name="shared-err")
    calls = 0
    started = threading.Event()
    release = threading.Event()

    def boom() -> int:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=1.0)
        raise RuntimeError("429")

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            cache.get_or_load("k", boom)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    assert started.wait(timeout=1.0)
    release.set()
    for t in threads:
        t.join(timeout=1.0)

    assert calls == 1
    assert len(errors) == 4
    assert all(isinstance(exc, RuntimeError) for exc in errors)
