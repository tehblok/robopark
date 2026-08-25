import threading
import time

import pytest

from robopark_api.services import emergency_cache, emergency_client
from robopark_api.services import platform_settings as settings_svc

VIN = "YASADR00000000447"


@pytest.fixture(autouse=True)
def empty_payload_cache():
    emergency_cache.clear_cache_for_tests()
    yield
    emergency_cache.clear_cache_for_tests()


def _set_cookie(db_session) -> None:
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")


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


def test_cache_expires_at_exactly_five_seconds(db_session, monkeypatch):
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
    now[0] += 4.999
    assert emergency_cache.get_robot_payload(db=db_session, vin=VIN) == {"call": 1}
    now[0] += 0.001
    assert emergency_cache.get_robot_payload(db=db_session, vin=VIN) == {"call": 2}
    assert calls == 2


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
    monkeypatch.setattr(settings_svc, "get_emergency_cookie", lambda db: "cookie")
    monkeypatch.setattr(settings_svc, "set_emergency_cookie_valid", lambda db, valid: None)
    monkeypatch.setattr(settings_svc, "touch_keepalive_ring", lambda db, vin: None)

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


def test_different_vins_do_not_share_flight(monkeypatch):
    barrier = threading.Barrier(2)
    calls = []

    def fake_fetch(**kwargs):
        calls.append(kwargs["vin"])
        barrier.wait(timeout=2)
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "get_emergency_cookie", lambda db: "cookie")
    monkeypatch.setattr(settings_svc, "set_emergency_cookie_valid", lambda db, valid: None)
    monkeypatch.setattr(settings_svc, "touch_keepalive_ring", lambda db, vin: None)

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


def test_cookie_valid_write_failure_releases_auth_error_waiters(monkeypatch):
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    errors = []

    def fake_fetch(**kwargs):
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        raise emergency_client.EmergencyAuthError("expired")

    def fail_cookie_valid_write(db, valid):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(settings_svc, "get_emergency_cookie", lambda db: "cookie")
    monkeypatch.setattr(settings_svc, "set_emergency_cookie_valid", fail_cookie_valid_write)

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
