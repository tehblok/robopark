import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from robopark_api.models import Report
from robopark_api.services import emergency_cache, emergency_client, reports
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.live_merge import LiveMergeStore

VIN = "YASADR00000000447"


@pytest.fixture(autouse=True)
def empty_payload_cache():
    emergency_cache.clear_cache_for_tests()
    yield
    emergency_cache.clear_cache_for_tests()


def _set_cookie(db_session) -> None:
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")


@pytest.mark.parametrize("outcome", ["valid", "invalid", "unavailable"])
def test_background_probe_keeps_public_metadata_consistent(db_session, monkeypatch, outcome):
    settings_svc.activate_emergency_cookie(
        db_session, cookie="cookie", status="valid", checked_robot="448"
    )
    before = settings_svc.get_emergency_cookie_checked_at(db_session)
    failure = {
        "invalid": emergency_client.EmergencyAuthError(),
        "unavailable": emergency_client.EmergencyError(),
    }.get(outcome)
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        Mock(side_effect=failure, return_value={"vin": VIN}),
    )
    if failure is not None:
        with pytest.raises(type(failure)):
            emergency_cache.get_robot_payload(db=db_session, vin=VIN)
    else:
        emergency_cache.get_robot_payload(db=db_session, vin=VIN)

    assert settings_svc.get_emergency_cookie_status(db_session) == outcome
    assert settings_svc.get_emergency_cookie_valid(db_session) is (outcome != "invalid")
    assert settings_svc.get_emergency_cookie_checked_robot(db_session) == "447"
    assert settings_svc.get_emergency_cookie_checked_at(db_session) != before


def test_cache_reuses_payload_within_ttl(db_session, monkeypatch):
    calls = 0

    def fake_fetch(**kwargs):
        nonlocal calls
        calls += 1
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    _set_cookie(db_session)

    first = emergency_cache.get_robot_payload(db=db_session, vin=VIN)
    second = emergency_cache.get_robot_payload(db=db_session, vin=VIN)

    assert first == second == {"vin": VIN}
    assert calls == 1


def test_cache_expires_at_exactly_three_seconds(db_session, monkeypatch):
    now = [100.0]
    calls = 0

    def fake_fetch(**kwargs):
        nonlocal calls
        calls += 1
        return {"call": calls}

    monkeypatch.setattr(emergency_cache.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    _set_cookie(db_session)

    assert emergency_cache.get_robot_payload(db=db_session, vin=VIN) == {"call": 1}
    now[0] += 2.999
    assert emergency_cache.get_robot_payload(db=db_session, vin=VIN) == {"call": 1}
    now[0] += 0.001
    assert emergency_cache.get_robot_payload(db=db_session, vin=VIN) == {"call": 2}
    assert calls == 2


def test_payload_cache_is_a_512_entry_lru(monkeypatch):
    calls: list[str] = []

    def fake_fetch(**kwargs):
        calls.append(kwargs["vin"])
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)
    probe = ("cookie", "identity")
    vins = [f"VIN-{index:03d}" for index in range(513)]

    for vin in vins:
        emergency_cache.get_robot_payload(db=object(), vin=vin, probe=probe)
    assert emergency_cache.get_robot_payload(db=object(), vin=vins[1], probe=probe) == {
        "vin": vins[1]
    }
    emergency_cache.get_robot_payload(db=object(), vin=vins[0], probe=probe)

    assert len(emergency_cache._cache) == 512
    assert calls.count(vins[1]) == 1
    assert calls.count(vins[0]) == 2


def test_distinct_vins_respect_configured_external_concurrency(monkeypatch):
    from robopark_api.config import reset_settings_cache

    limit = 3
    callers = 20
    ready = threading.Barrier(callers + 1)
    saturated = threading.Event()
    overflowed = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    active = 0
    peak = 0

    monkeypatch.setenv("EMERGENCY_MAX_CONCURRENCY", str(limit))
    reset_settings_cache()

    def fake_fetch(**kwargs):
        nonlocal active, peak
        with state_lock:
            active += 1
            peak = max(peak, active)
            if active == limit:
                saturated.set()
            if active > limit:
                overflowed.set()
        assert release.wait(timeout=5)
        with state_lock:
            active -= 1
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)

    def worker(index: int) -> dict:
        ready.wait(timeout=5)
        return emergency_cache.get_robot_payload(
            db=object(), vin=f"VIN-{index}", probe=("cookie", "identity")
        )

    with ThreadPoolExecutor(max_workers=callers) as executor:
        futures = [executor.submit(worker, index) for index in range(callers)]
        ready.wait(timeout=5)
        assert saturated.wait(timeout=5)
        overflow_before_release = overflowed.wait(timeout=0.2)
        release.set()
        results = [future.result(timeout=5) for future in futures]

    assert len(results) == callers
    assert not overflow_before_release
    assert peak == limit
    assert emergency_cache._flights == {}


def test_single_flight_per_vin(monkeypatch):
    release_fetch = threading.Event()
    fetch_started = threading.Event()
    calls = 0

    def fake_fetch(**kwargs):
        nonlocal calls
        calls += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "get_emergency_cookie_probe", lambda db: ("cookie", "test"))
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: True)
    monkeypatch.setattr(reports, "resolve_open_emergency_cookie_reports", lambda db, **kwargs: 0)

    results = []
    errors = []

    def load():
        try:
            results.append(emergency_cache.get_robot_payload(db=object(), vin=VIN))
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=load)
    second = threading.Thread(target=load)
    first.start()
    assert fetch_started.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    release_fetch.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not errors
    assert results == [{"vin": VIN}, {"vin": VIN}]
    assert calls == 1


def test_two_hundred_same_vin_callers_share_one_payload_object(monkeypatch):
    callers = 200
    ready = threading.Barrier(callers + 1)
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    payload = {"vin": VIN}
    calls = 0

    def fake_fetch(**kwargs):
        nonlocal calls
        calls += 1
        fetch_started.set()
        assert release_fetch.wait(timeout=5)
        return payload

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)

    def worker() -> dict:
        ready.wait(timeout=5)
        return emergency_cache.get_robot_payload(db=object(), vin=VIN, probe=("cookie", "identity"))

    with ThreadPoolExecutor(max_workers=callers) as executor:
        futures = [executor.submit(worker) for _ in range(callers)]
        ready.wait(timeout=5)
        assert fetch_started.wait(timeout=5)
        release_fetch.set()
        results = [future.result(timeout=5) for future in futures]

    assert calls == 1
    assert all(result is payload for result in results)
    assert emergency_cache._flights == {}


def test_different_vins_do_not_share_flight(monkeypatch):
    barrier = threading.Barrier(2)
    calls = []

    def fake_fetch(**kwargs):
        calls.append(kwargs["vin"])
        barrier.wait(timeout=2)
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "get_emergency_cookie_probe", lambda db: ("cookie", "test"))
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: True)
    monkeypatch.setattr(reports, "resolve_open_emergency_cookie_reports", lambda db, **kwargs: 0)

    results = []
    threads = [
        threading.Thread(
            target=lambda vin=vin: results.append(
                emergency_cache.get_robot_payload(db=object(), vin=vin)
            )
        )
        for vin in ("VIN-1", "VIN-2")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(calls) == ["VIN-1", "VIN-2"]
    assert sorted(result["vin"] for result in results) == ["VIN-1", "VIN-2"]


def test_auth_error_invalidates_vin_marks_cookie_invalid_and_reraises(db_session, monkeypatch):
    now = [100.0]
    responses = iter(({"version": 1}, emergency_client.EmergencyAuthError("expired")))

    def fake_fetch(**kwargs):
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(emergency_cache.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    _set_cookie(db_session)

    assert emergency_cache.get_robot_payload(db=db_session, vin=VIN) == {"version": 1}
    now[0] += 5.0
    with pytest.raises(emergency_client.EmergencyAuthError, match="expired"):
        emergency_cache.get_robot_payload(db=db_session, vin=VIN)

    assert settings_svc.get_emergency_cookie_valid(db_session) is False
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: {"version": 2},
    )
    assert emergency_cache.get_robot_payload(db=db_session, vin=VIN) == {"version": 2}


def test_unavailable_refresh_returns_last_good_only_before_sixty_seconds(db_session, monkeypatch):
    now = [100.0]
    responses = iter(({"version": 1}, emergency_client.EmergencyError("offline")))

    def fake_fetch(**kwargs):
        response = next(responses, emergency_client.EmergencyError("offline"))
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(emergency_cache.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)
    probe = ("cookie", "identity")

    first = emergency_cache.get_robot_payload(db=db_session, vin=VIN, probe=probe)
    now[0] = 104.0
    assert emergency_cache.get_robot_payload(db=db_session, vin=VIN, probe=probe) is first
    now[0] = 160.0
    with pytest.raises(emergency_client.EmergencyError, match="offline"):
        emergency_cache.get_robot_payload(db=db_session, vin=VIN, probe=probe)


def test_payload_result_identifies_last_good_age_without_changing_legacy_return(
    db_session, monkeypatch
):
    now = [100.0]
    responses = iter(({"version": 1}, emergency_client.EmergencyError("offline")))

    def fake_fetch(**kwargs):
        response = next(responses)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(emergency_cache.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", lambda _db, **kwargs: False)
    probe = ("cookie", "identity")

    fresh = emergency_cache.get_robot_payload_result(db=db_session, vin=VIN, probe=probe)
    assert fresh.payload == {"version": 1}
    assert fresh.stale is False
    assert fresh.age_seconds == 0

    now[0] = 104.25
    stale = emergency_cache.get_robot_payload_result(db=db_session, vin=VIN, probe=probe)
    assert stale.payload == {"version": 1}
    assert stale.stale is True
    assert stale.age_seconds == pytest.approx(4.25)


@pytest.mark.parametrize("failure", [RuntimeError("boom"), KeyboardInterrupt()])
def test_failed_emergency_flight_is_removed(monkeypatch, failure):
    def fail(**kwargs):
        raise failure

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fail)
    with pytest.raises(type(failure)):
        emergency_cache.get_robot_payload(db=object(), vin=VIN, probe=("cookie", "identity"))

    assert emergency_cache._flights == {}


def test_cookie_valid_write_failure_releases_auth_error_waiters(monkeypatch):
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    errors = []

    def fake_fetch(**kwargs):
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        raise emergency_client.EmergencyAuthError("expired")

    def fail_probe_record(_db, **kwargs):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "get_emergency_cookie_probe", lambda db: ("cookie", "test"))
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", fail_probe_record)

    def load():
        try:
            emergency_cache.get_robot_payload(db=object(), vin=VIN)
        except BaseException as exc:
            errors.append(exc)

    leader = threading.Thread(target=load)
    waiter = threading.Thread(target=load)
    leader.start()
    assert fetch_started.wait(timeout=2)
    waiter.start()
    release_fetch.set()
    leader.join(timeout=2)
    waiter.join(timeout=2)

    assert not leader.is_alive()
    assert not waiter.is_alive()
    assert len(errors) == 2


def test_success_marks_cookie_valid_and_touches_ring(db_session, monkeypatch):
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: {"vin": kwargs["vin"]},
    )
    _set_cookie(db_session)
    settings_svc.set_emergency_cookie_valid(db_session, False)

    emergency_cache.get_robot_payload(db=db_session, vin=VIN)

    assert settings_svc.get_emergency_cookie_valid(db_session) is True
    assert settings_svc.get_keepalive_ring(db_session) == [VIN]


def test_cache_hit_does_not_record_probe_side_effects_twice(db_session, monkeypatch):
    records = 0

    def fake_record(_db, **kwargs):
        nonlocal records
        records += 1
        return True

    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: {"vin": kwargs["vin"]},
    )
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", fake_record)
    _set_cookie(db_session)

    emergency_cache.get_robot_payload(db=db_session, vin=VIN)
    emergency_cache.get_robot_payload(db=db_session, vin=VIN)

    assert records == 1


def test_clear_cache_discards_stale_flight_result_and_side_effects(tmp_path, monkeypatch):
    store = LiveMergeStore(tmp_path)
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    results = []
    record_probe = Mock(return_value=True)
    resolved_calls = []

    def fake_fetch(**_kwargs):
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        return {"generation": "old"}

    monkeypatch.setattr(emergency_cache, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", record_probe)
    monkeypatch.setattr(
        settings_svc, "get_emergency_cookie_probe", lambda db: ("old-cookie", "old")
    )
    monkeypatch.setattr(
        reports,
        "resolve_open_emergency_cookie_reports",
        lambda db, **kwargs: resolved_calls.append(db),
    )

    thread = threading.Thread(
        target=lambda: results.append(emergency_cache.get_robot_payload(db=object(), vin=VIN))
    )
    thread.start()
    assert fetch_started.wait(timeout=2)
    emergency_cache.clear_cache()
    release_fetch.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert results == [{"generation": "old"}]
    record_probe.assert_not_called()
    assert resolved_calls == []
    assert VIN not in emergency_cache._cache
    assert store.try_fresh(
        emergency_cache._MERGE_NS, f"old:{VIN}", emergency_cache.PAYLOAD_CACHE_TTL_SECONDS
    ) == (
        False,
        None,
    )


def test_clear_cache_discards_stale_flight_auth_error_side_effects(tmp_path, monkeypatch):
    store = LiveMergeStore(tmp_path)
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    errors = []
    record_probe = Mock(return_value=True)
    reports_opened = []

    def fake_fetch(**_kwargs):
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        raise emergency_client.EmergencyAuthError("expired")

    monkeypatch.setattr(emergency_cache, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "record_emergency_cookie_probe", record_probe)
    monkeypatch.setattr(
        settings_svc, "get_emergency_cookie_probe", lambda db: ("old-cookie", "old")
    )
    monkeypatch.setattr(
        reports,
        "ensure_open_emergency_cookie_report",
        lambda db, author, **kwargs: reports_opened.append(db),
    )

    def load():
        try:
            emergency_cache.get_robot_payload(db=object(), vin=VIN)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=load)
    thread.start()
    assert fetch_started.wait(timeout=2)
    emergency_cache.clear_cache()
    release_fetch.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], emergency_client.EmergencyAuthError)
    record_probe.assert_not_called()
    assert reports_opened == []
    assert not store.error_path(emergency_cache._MERGE_NS, f"old:{VIN}").exists()


def test_other_worker_stale_success_cannot_be_accepted_after_cookie_activation(
    db_engine, seed_royal, tmp_path, monkeypatch
):
    store = LiveMergeStore(tmp_path)
    with Session(db_engine) as setup:
        settings_svc.activate_emergency_cookie(
            setup,
            cookie="old-cookie",
            status="valid",
            checked_robot="447",
        )
        old_identity = settings_svc.get_emergency_cookie_identity(setup)
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    old_results = []

    def fake_fetch(*, cookie, vin):
        if cookie == "old-cookie":
            fetch_started.set()
            assert release_fetch.wait(timeout=2)
            return {"source": "old", "vin": vin}
        return {"source": "new", "vin": vin}

    monkeypatch.setattr(emergency_cache, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)

    def old_worker_load():
        with Session(db_engine) as old_worker_session:
            old_results.append(emergency_cache.get_robot_payload(db=old_worker_session, vin=VIN))

    old_worker = threading.Thread(target=old_worker_load)
    old_worker.start()
    assert fetch_started.wait(timeout=2)

    with Session(db_engine) as replacement:
        new_identity = settings_svc.activate_emergency_cookie(
            replacement,
            cookie="new-cookie",
            status="valid",
            checked_robot="447",
        )
        assert (
            reports.ensure_open_emergency_cookie_report(
                replacement,
                author=None,
                expected_identity=new_identity,
            )
            is not None
        )
    release_fetch.set()
    old_worker.join(timeout=2)

    assert not old_worker.is_alive()
    assert old_results == [{"source": "old", "vin": VIN}]
    with Session(db_engine) as check:
        assert settings_svc.get_emergency_cookie_valid(check) is True
        assert settings_svc.get_keepalive_ring(check) == []
        assert check.query(Report).one().status == "open"
        assert store.try_fresh(
            emergency_cache._MERGE_NS,
            emergency_cache._shared_key(new_identity, VIN),
            emergency_cache.PAYLOAD_CACHE_TTL_SECONDS,
        ) == (False, None)
        assert store.try_fresh(
            emergency_cache._MERGE_NS,
            emergency_cache._shared_key(old_identity, VIN),
            emergency_cache.PAYLOAD_CACHE_TTL_SECONDS,
        )[0]

    with Session(db_engine) as new_worker_session:
        assert emergency_cache.get_robot_payload(db=new_worker_session, vin=VIN) == {
            "source": "new",
            "vin": VIN,
        }


def test_other_worker_stale_auth_error_cannot_invalidate_new_cookie(
    db_engine, seed_royal, tmp_path, monkeypatch
):
    store = LiveMergeStore(tmp_path)
    with Session(db_engine) as setup:
        settings_svc.activate_emergency_cookie(
            setup,
            cookie="old-cookie",
            status="valid",
            checked_robot="447",
        )
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    errors = []

    def fake_fetch(*, cookie, vin):
        if cookie == "old-cookie":
            fetch_started.set()
            assert release_fetch.wait(timeout=2)
            raise emergency_client.EmergencyAuthError("expired")
        return {"source": "new", "vin": vin}

    monkeypatch.setattr(emergency_cache, "get_live_merge_store", lambda: store)
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)

    def old_worker_load():
        with Session(db_engine) as old_worker_session:
            try:
                emergency_cache.get_robot_payload(db=old_worker_session, vin=VIN)
            except BaseException as exc:
                errors.append(exc)

    old_worker = threading.Thread(target=old_worker_load)
    old_worker.start()
    assert fetch_started.wait(timeout=2)
    with Session(db_engine) as replacement:
        settings_svc.activate_emergency_cookie(
            replacement,
            cookie="new-cookie",
            status="valid",
            checked_robot="447",
        )
    release_fetch.set()
    old_worker.join(timeout=2)

    assert not old_worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], emergency_client.EmergencyAuthError)
    with Session(db_engine) as check:
        assert settings_svc.get_emergency_cookie_valid(check) is True
        assert settings_svc.get_keepalive_ring(check) == []
        assert check.query(Report).count() == 0


def test_keepalive_ring_moves_vin_to_end_and_keeps_latest_twenty(db_session):
    for number in range(21):
        settings_svc.touch_keepalive_ring(db_session, f"VIN-{number}")
    settings_svc.touch_keepalive_ring(db_session, "VIN-5")

    ring = settings_svc.get_keepalive_ring(db_session)

    assert len(ring) == 20
    assert ring[0] == "VIN-1"
    assert ring[-1] == "VIN-5"
    assert ring.count("VIN-5") == 1


def test_keepalive_ring_updates_are_serialized(monkeypatch):
    persisted = []
    first_read = threading.Event()
    release_first_read = threading.Event()
    read_count = 0

    def fake_get_ring(db):
        nonlocal read_count
        snapshot = list(persisted)
        read_count += 1
        if read_count == 1:
            first_read.set()
            assert release_first_read.wait(timeout=2)
        return snapshot

    def fake_set_setting(db, key, value):
        persisted[:] = settings_svc.json.loads(value)

    monkeypatch.setattr(settings_svc, "get_keepalive_ring", fake_get_ring)
    monkeypatch.setattr(settings_svc, "set_setting", fake_set_setting)

    first = threading.Thread(target=settings_svc.touch_keepalive_ring, args=(object(), "VIN-1"))
    second = threading.Thread(target=settings_svc.touch_keepalive_ring, args=(object(), "VIN-2"))
    first.start()
    assert first_read.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    release_first_read.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert persisted == ["VIN-1", "VIN-2"]
