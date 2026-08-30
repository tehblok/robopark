import threading
import time

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
