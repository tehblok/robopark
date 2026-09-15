"""Deterministic load verification for the Emergency single-flight boundary.

The one-hour RSS soak is intentionally opt-in. Release command:

    ROBOPARK_RUN_ONE_HOUR_RSS_SOAK=1 uv run --frozen --extra dev \
      python -m pytest -p no:cacheprovider -q tests/test_emergency_load.py \
      -k one_hour_rss_soak
"""

from __future__ import annotations

import os
import resource
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from robopark_api.services import (
    emergency_cache,
    emergency_client,
)
from robopark_api.services import (
    platform_settings as settings_svc,
)
from robopark_api.services.live_merge import LiveMergeStore

IDENTITY = ("load-cookie", "load-identity")


def _load(vin: str) -> dict[str, str]:
    return emergency_cache.get_robot_payload(db=object(), vin=vin, probe=IDENTITY)


def test_two_hundred_callers_for_one_vin_share_one_upstream_call(monkeypatch):
    callers = 200
    vin = "YASADR00000000447"
    ready = threading.Barrier(callers + 1)
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    payload = {"vin": vin}
    calls = 0

    def fake_fetch(**kwargs):
        nonlocal calls
        calls += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=10)
        return payload

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)

    def caller() -> dict[str, str]:
        ready.wait(timeout=10)
        return _load(vin)

    with ThreadPoolExecutor(max_workers=callers) as executor:
        futures = [executor.submit(caller) for _ in range(callers)]
        ready.wait(timeout=10)
        assert fetch_started.wait(timeout=10)
        release_fetch.set()
        results = [future.result(timeout=10) for future in futures]

    assert calls == 1
    assert all(result is payload for result in results)
    assert emergency_cache._flights == {}


def test_two_hundred_callers_across_twenty_vins_obey_external_limit(monkeypatch):
    from robopark_api.config import reset_settings_cache

    callers = 200
    vins = [f"YASADR00000000{index:03d}" for index in range(20)]
    limit = 4
    ready = threading.Barrier(callers + 1)
    saturated = threading.Event()
    release_fetches = threading.Event()
    state_lock = threading.Lock()
    upstream_calls: Counter[str] = Counter()
    active = 0
    peak = 0

    monkeypatch.setenv("EMERGENCY_MAX_CONCURRENCY", str(limit))
    reset_settings_cache()

    def fake_fetch(**kwargs):
        nonlocal active, peak
        vin = kwargs["vin"]
        with state_lock:
            upstream_calls[vin] += 1
            active += 1
            peak = max(peak, active)
            if active == limit:
                saturated.set()
        assert release_fetches.wait(timeout=10)
        with state_lock:
            active -= 1
        return {"vin": vin}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)

    def caller(index: int) -> dict[str, str]:
        ready.wait(timeout=10)
        return _load(vins[index % len(vins)])

    with ThreadPoolExecutor(max_workers=callers) as executor:
        futures = [executor.submit(caller, index) for index in range(callers)]
        ready.wait(timeout=10)
        assert saturated.wait(timeout=10)
        release_fetches.set()
        results = [future.result(timeout=10) for future in futures]

    assert Counter(result["vin"] for result in results) == Counter(
        {vin: callers // len(vins) for vin in vins}
    )
    assert upstream_calls == Counter(dict.fromkeys(vins, 1))
    assert peak == limit
    assert emergency_cache._flights == {}


def test_repeated_bounded_key_and_live_merge_cycles_plateau(tmp_path, monkeypatch):
    store = LiveMergeStore(tmp_path / "live-merge")
    vins = [f"YASADR00000000{index:03d}" for index in range(24)]
    file_counts: list[int] = []

    monkeypatch.setattr(emergency_cache, "PAYLOAD_CACHE_TTL_SECONDS", 0.0)
    monkeypatch.setattr(emergency_cache, "PAYLOAD_CACHE_MAX_ENTRIES", 16)
    monkeypatch.setattr(emergency_cache, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: {"vin": kwargs["vin"]},
    )
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)

    for _ in range(3):
        for vin in vins:
            assert _load(vin) == {"vin": vin}
        file_counts.append(sum(path.is_file() for path in store.root.rglob("*")))
        assert len(emergency_cache._cache) == 16
        assert emergency_cache._flights == {}

    assert file_counts == [48, 48, 48]
    assert not list(store.root.rglob("*.inflight"))
    assert not list(store.root.rglob("*.error"))
    assert not list(store.root.rglob("*.tmp"))


def _rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if os.uname().sysname == "Darwin" else value * 1024


@pytest.mark.skipif(
    os.getenv("ROBOPARK_RUN_ONE_HOUR_RSS_SOAK") != "1",
    reason="release-only one-hour RSS soak",
)
def test_one_hour_rss_soak_plateaus_after_warmup(tmp_path, monkeypatch):
    store = LiveMergeStore(tmp_path / "live-merge")
    vins = [f"YASADR00000000{index:03d}" for index in range(20)]
    monkeypatch.setattr(emergency_cache, "PAYLOAD_CACHE_TTL_SECONDS", 0.0)
    monkeypatch.setattr(emergency_cache, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: {"vin": kwargs["vin"]},
    )
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)

    started = time.monotonic()
    warm_rss = 0
    while time.monotonic() - started < 3600:
        for vin in vins:
            _load(vin)
        elapsed = time.monotonic() - started
        if warm_rss == 0 and elapsed >= 1800:
            warm_rss = _rss_bytes()
        threading.Event().wait(1)

    assert warm_rss > 0
    assert _rss_bytes() <= warm_rss * 1.10
    assert len(emergency_cache._cache) <= emergency_cache.PAYLOAD_CACHE_MAX_ENTRIES
    assert sum(path.is_file() for path in store.root.rglob("*")) == len(vins) * 2
    assert emergency_cache._flights == {}
