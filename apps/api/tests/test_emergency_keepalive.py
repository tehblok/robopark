import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from robopark_api import main
from robopark_api.services import emergency_client
from robopark_api.services import emergency_keepalive
from robopark_api.services import platform_settings as settings_svc


def test_empty_ring_without_seed_skips_upstream(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: calls.append(kwargs),
    )

    emergency_keepalive.keepalive_once(db_session)

    assert calls == []


def test_ring_vin_fetches_and_records_success(db_session, monkeypatch):
    vin = "YASADR00000000447"
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
    settings_svc.touch_keepalive_ring(db_session, vin)
    calls = []

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)

    emergency_keepalive.keepalive_once(db_session)

    assert calls == [{"cookie": "cookie", "vin": vin}]
    assert settings_svc.get_emergency_cookie_valid(db_session) is True
    assert (
        settings_svc.get_setting(
            db_session, settings_svc.EMERGENCY_KEEPALIVE_LAST_OK_KEY
        )
        is not None
    )


def test_seed_vin_is_used_when_ring_is_empty(db_session, monkeypatch):
    vin = "YASADR00000000448"
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
    settings_svc.set_setting(
        db_session, settings_svc.EMERGENCY_KEEPALIVE_SEED_VIN_KEY, vin
    )
    calls = []
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: calls.append(kwargs) or {},
    )

    emergency_keepalive.keepalive_once(db_session)

    assert calls == [{"cookie": "cookie", "vin": vin}]


def test_missing_cookie_skips_ring(db_session, monkeypatch):
    settings_svc.touch_keepalive_ring(db_session, "YASADR00000000450")
    calls = []
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: calls.append(kwargs),
    )

    emergency_keepalive.keepalive_once(db_session)

    assert calls == []


def test_unauthorized_fetch_marks_cookie_invalid(db_session, monkeypatch):
    vin = "YASADR00000000449"
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
    settings_svc.touch_keepalive_ring(db_session, vin)

    def unauthorized(**_kwargs):
        raise emergency_client.EmergencyAuthError("invalid")

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", unauthorized)

    emergency_keepalive.keepalive_once(db_session)

    assert settings_svc.get_emergency_cookie_valid(db_session) is False


def test_loop_accepts_injectable_interval(monkeypatch):
    asyncio_stop = asyncio.Event()
    calls = []

    def fake_keepalive_once(*, stop_event=None):
        calls.append(stop_event)
        asyncio_stop.set()

    monkeypatch.setattr(emergency_keepalive, "keepalive_once", fake_keepalive_once)

    asyncio.run(
        emergency_keepalive.run_keepalive_loop(asyncio_stop, interval_seconds=0)
    )

    assert len(calls) == 1


def test_keepalive_once_stops_between_vins_when_requested(db_session, monkeypatch):
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
    for vin in ("VIN-1", "VIN-2", "VIN-3"):
        settings_svc.touch_keepalive_ring(db_session, vin)

    calls = []
    stop_event = threading.Event()

    def fake_fetch(**kwargs):
        calls.append(kwargs["vin"])
        if len(calls) == 1:
            stop_event.set()

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(
        emergency_keepalive, "INTER_VIN_GAP_SECONDS", 5.0, raising=False
    )

    emergency_keepalive.keepalive_once(db_session, stop_event=stop_event)

    assert calls == ["VIN-1"]


def test_run_keepalive_loop_sets_thread_stop_on_cancel(monkeypatch):
    captured_stop: list[threading.Event] = []

    def fake_keepalive_once(*, stop_event=None):
        captured_stop.append(stop_event)
        while not stop_event.is_set():
            time.sleep(0.01)

    monkeypatch.setattr(emergency_keepalive, "keepalive_once", fake_keepalive_once)

    async def run_and_cancel():
        stop_event = asyncio.Event()
        task = asyncio.create_task(emergency_keepalive.run_keepalive_loop(stop_event))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())
    assert captured_stop
    assert captured_stop[0].is_set()


def test_lifespan_starts_and_stops_keepalive(
    db_engine, test_settings, monkeypatch
):
    started = threading.Event()
    stopped = threading.Event()

    async def fake_loop(stop_event):
        started.set()
        try:
            await stop_event.wait()
        finally:
            stopped.set()

    monkeypatch.setattr(main, "run_keepalive_loop", fake_loop, raising=False)
    monkeypatch.setattr(
        main, "SessionLocal", sessionmaker(bind=db_engine, future=True)
    )
    monkeypatch.setattr(main, "get_settings", lambda: test_settings)

    with TestClient(main.create_app()):
        assert started.wait(timeout=1)

    assert stopped.wait(timeout=1)
