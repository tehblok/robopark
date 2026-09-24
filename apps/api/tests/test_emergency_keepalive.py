import asyncio
import threading
import time

import pytest
from sqlalchemy.orm import sessionmaker

from robopark_api.models import Report
from robopark_api.services import emergency_client, emergency_keepalive, reports, worker_runtime
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
        settings_svc.get_setting(db_session, settings_svc.EMERGENCY_KEEPALIVE_LAST_OK_KEY)
        is not None
    )


def test_keepalive_shares_emergency_cache(db_session, monkeypatch):
    vin = "YASADR00000000447"
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
    settings_svc.touch_keepalive_ring(db_session, vin)
    calls = []

    def fake_fetch(**kwargs):
        calls.append(kwargs)
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    emergency_keepalive.keepalive_once(db_session)
    emergency_keepalive.keepalive_once(db_session)
    assert len(calls) == 1


def test_seed_vin_is_used_when_ring_is_empty(db_session, monkeypatch):
    vin = "YASADR00000000448"
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "cookie")
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_KEEPALIVE_SEED_VIN_KEY, vin)
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
    settings_svc.activate_emergency_cookie(
        db_session, cookie="cookie", status="valid", checked_robot="447"
    )
    settings_svc.touch_keepalive_ring(db_session, vin)

    def unauthorized(**_kwargs):
        raise emergency_client.EmergencyAuthError("invalid")

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", unauthorized)

    emergency_keepalive.keepalive_once(db_session)

    assert settings_svc.get_emergency_cookie_valid(db_session) is False
    assert settings_svc.get_emergency_cookie_status(db_session) == "invalid"
    assert settings_svc.get_emergency_cookie_checked_robot(db_session) == "449"


def test_cached_keepalive_cannot_undo_newer_recheck_failure(db_session, seed_royal, monkeypatch):
    vin = "YASADR00000000449"
    identity = settings_svc.activate_emergency_cookie(
        db_session, cookie="cookie", status="valid", checked_robot="449"
    )
    settings_svc.touch_keepalive_ring(db_session, vin)
    monkeypatch.setattr(
        emergency_client, "fetch_robot_payload", lambda **kwargs: {"vin": kwargs["vin"]}
    )
    emergency_keepalive.keepalive_once(db_session)
    settings_svc.record_emergency_cookie_probe(
        db_session, identity=identity, valid=False, status="invalid", checked_robot="449"
    )
    reports.ensure_open_emergency_cookie_report(db_session, author=None, expected_identity=identity)
    checked_at = settings_svc.get_emergency_cookie_checked_at(db_session)

    emergency_keepalive.keepalive_once(db_session)

    assert settings_svc.get_emergency_cookie_valid(db_session) is False
    assert settings_svc.get_emergency_cookie_status(db_session) == "invalid"
    assert settings_svc.get_emergency_cookie_checked_at(db_session) == checked_at
    assert db_session.query(Report).one().status == "open"


def test_stale_keepalive_success_does_not_update_new_cookie_or_resolve_report(
    db_engine, seed_royal, monkeypatch
):
    vin = "YASADR00000000449"
    with sessionmaker(bind=db_engine, future=True)() as setup:
        settings_svc.activate_emergency_cookie(
            setup,
            cookie="old-cookie",
            status="valid",
            checked_robot="449",
        )
        settings_svc.touch_keepalive_ring(setup, vin)

    fetch_started = threading.Event()
    release_fetch = threading.Event()

    def slow_fetch(*, cookie, vin):
        assert cookie == "old-cookie"
        fetch_started.set()
        assert release_fetch.wait(timeout=2)
        return {"vin": vin}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", slow_fetch)

    def old_worker():
        with sessionmaker(bind=db_engine, future=True)() as old_session:
            emergency_keepalive.keepalive_once(old_session)

    worker = threading.Thread(target=old_worker)
    worker.start()
    assert fetch_started.wait(timeout=2)
    with sessionmaker(bind=db_engine, future=True)() as replacement:
        new_identity = settings_svc.activate_emergency_cookie(
            replacement,
            cookie="new-cookie",
            status="valid",
            checked_robot="449",
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
    worker.join(timeout=2)

    assert not worker.is_alive()
    with sessionmaker(bind=db_engine, future=True)() as check:
        assert settings_svc.get_setting(check, settings_svc.EMERGENCY_KEEPALIVE_LAST_OK_KEY) is None
        assert check.query(Report).one().status == "open"


def test_loop_accepts_injectable_interval(monkeypatch):
    asyncio_stop = asyncio.Event()
    calls = []

    def fake_keepalive_once(*, stop_event=None):
        calls.append(stop_event)
        asyncio_stop.set()

    monkeypatch.setattr(emergency_keepalive, "keepalive_once", fake_keepalive_once)

    asyncio.run(emergency_keepalive.run_keepalive_loop(asyncio_stop, interval_seconds=0))

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
        return {"vin": kwargs["vin"]}

    monkeypatch.setattr(emergency_client, "fetch_robot_payload", fake_fetch)
    monkeypatch.setattr(emergency_keepalive, "INTER_VIN_GAP_SECONDS", 5.0, raising=False)

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


def test_run_keepalive_loop_bridges_shutdown_to_worker_and_joins_it(monkeypatch):
    """Normal lifespan shutdown must interrupt and join the real worker thread."""
    worker_started = threading.Event()
    worker_saw_stop = threading.Event()
    worker_returned = threading.Event()

    def blocking_keepalive_once(*, stop_event=None):
        worker_started.set()
        if stop_event.wait(timeout=0.5):
            worker_saw_stop.set()
        worker_returned.set()

    monkeypatch.setattr(emergency_keepalive, "keepalive_once", blocking_keepalive_once)

    async def stop_while_worker_runs():
        stop_event = asyncio.Event()
        task = asyncio.create_task(emergency_keepalive.run_keepalive_loop(stop_event))
        assert await asyncio.to_thread(worker_started.wait, 0.2)
        started = time.monotonic()
        stop_event.set()
        await asyncio.wait_for(task, timeout=0.3)
        return time.monotonic() - started

    elapsed = asyncio.run(stop_while_worker_runs())

    assert elapsed < 0.3
    assert worker_saw_stop.is_set()
    assert worker_returned.is_set()


def test_worker_starts_and_stops_keepalive(db_engine, test_settings, monkeypatch):
    started = threading.Event()
    stopped = threading.Event()

    async def fake_loop(stop_event):
        started.set()
        try:
            await stop_event.wait()
        finally:
            stopped.set()

    async def idle(*args, **kwargs):
        stop = next(arg for arg in args if isinstance(arg, asyncio.Event))
        await stop.wait()

    class Lease:
        def __init__(self, root, name):
            del root, name

        def try_acquire(self):
            return True

        def release(self):
            pass

    class Push:
        def emit(self, **kwargs):
            return {}

        def close(self):
            pass

    monkeypatch.setattr(worker_runtime, "run_keepalive_loop", fake_loop)
    monkeypatch.setattr(worker_runtime, "JobLease", Lease)
    monkeypatch.setattr(worker_runtime, "host_maintenance_active", lambda _settings: False)
    for name in (
        "run_blocker_history_loop",
        "run_session_cleanup_loop",
        "run_cache_cleanup_loop",
        "run_system_notification_loop",
        "run_tracker_outbox_loop",
        "run_campaign_refresh_loop",
        "run_tracker_notification_loop",
    ):
        monkeypatch.setattr(worker_runtime, name, idle)

    async def scenario():
        stop = asyncio.Event()
        factory = sessionmaker(bind=db_engine, future=True)
        task = asyncio.create_task(
            worker_runtime.WorkerRuntime(test_settings, factory, Push()).start(stop)
        )
        assert await asyncio.to_thread(started.wait, 1)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(scenario())
    assert stopped.is_set()
