import asyncio
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from robopark_api.ops_schemas import CheckOut, SystemHealthOut, UpdateOut
from robopark_api.routers.push import PushService
from robopark_api.schedule_models import NotificationEvent, SystemIncidentOccurrence
from robopark_api.services.ops import host_bridge
from robopark_api.services.system_notifications import (
    health_alerts,
    read_health_alerts,
    run_system_notification_loop,
)


def test_health_alerts_route_resources_integrations_and_update():
    alerts = health_alerts(
        SystemHealthOut(
            overall="degraded",
            checks=[
                CheckOut(code="resources", status="warning", message="Диск"),
                CheckOut(code="temperature", status="failed", message="Температура"),
                CheckOut(code="integrations", status="failed", message="Интеграции"),
            ],
            update=UpdateOut(state="rolled_back"),
        )
    )
    assert [item[0] for item in alerts] == [
        "disk_low",
        "anomaly",
        "network_problem",
        "update_failure",
    ]


def test_system_notification_loop_persists_each_health_occurrence_once(
    monkeypatch, db_engine, seed_royal
):
    stop = asyncio.Event()
    states = [
        [("server_problem", "system:degraded", "Система требует внимания")],
        [("server_problem", "system:degraded", "Система требует внимания")],
        [],
        [("server_problem", "system:degraded", "Система требует внимания")],
    ]

    def read(_settings):
        value = states.pop(0)
        if not states:
            stop.set()
        return value

    session_factory = sessionmaker(bind=db_engine, future=True)
    service = PushService(session_factory)
    monkeypatch.setattr("robopark_api.services.system_notifications.read_health_alerts", read)
    asyncio.run(
        run_system_notification_loop(
            stop,
            settings=object(),
            emit=service.emit,
            interval_seconds=0,
        )
    )

    with session_factory() as db:
        events = list(db.scalars(select(NotificationEvent)))
        occurrences = list(
            db.scalars(
                select(SystemIncidentOccurrence).order_by(SystemIncidentOccurrence.started_at)
            )
        )
    assert [event.event_type for event in events] == [
        "server_problem",
        "system_recovered",
        "server_problem",
    ]
    assert [event.user_id for event in events] == [seed_royal.id] * 3
    assert len(occurrences) == 2
    assert occurrences[0].resolved_at is not None
    assert occurrences[1].resolved_at is None


def test_system_recovery_is_royal_only_and_deduplicated_per_episode(
    db_engine, db_session, seed_royal, seed_admin
):
    session_factory = sessionmaker(bind=db_engine, future=True)
    service = PushService(session_factory)
    service.emit_for_tests(
        event_type="disk_low",
        park_id=None,
        protected_text="Диск",
        event_key="system:resources:failed",
    )

    service.sync_system_incidents(set())
    service.sync_system_incidents(set())

    events = list(
        db_session.scalars(
            select(NotificationEvent).order_by(NotificationEvent.created_at, NotificationEvent.id)
        )
    )
    assert [(event.event_type, event.user_id) for event in events] == [
        ("disk_low", seed_royal.id),
        ("system_recovered", seed_royal.id),
    ]
    assert all(event.user_id != seed_admin.id for event in events)


def test_read_health_alerts_distinguishes_unavailable_source(monkeypatch):
    def unavailable(_root):
        raise host_bridge.BridgeError("unavailable")

    monkeypatch.setattr(host_bridge, "host_root", lambda _settings: object())
    monkeypatch.setattr(host_bridge, "system_health", unavailable)

    assert read_health_alerts(SimpleNamespace(ops_host_root="/ops")) is None


def test_system_notification_loop_keeps_occurrence_open_while_source_unavailable(
    monkeypatch, db_engine, seed_royal
):
    stop = asyncio.Event()
    down = [("disk_low", "resources:failed", "Диск")]
    states = [down, None, down]

    def read(_settings):
        value = states.pop(0)
        if not states:
            stop.set()
        return value

    session_factory = sessionmaker(bind=db_engine, future=True)
    service = PushService(session_factory)
    monkeypatch.setattr("robopark_api.services.system_notifications.read_health_alerts", read)
    asyncio.run(
        run_system_notification_loop(
            stop,
            settings=object(),
            emit=service.emit,
            interval_seconds=0,
        )
    )

    with session_factory() as db:
        events = list(db.scalars(select(NotificationEvent)))
        occurrences = list(db.scalars(select(SystemIncidentOccurrence)))
    assert [event.user_id for event in events] == [seed_royal.id]
    assert len(occurrences) == 1
    assert occurrences[0].resolved_at is None


def test_severity_change_keeps_one_incident_until_a_confirmed_recovery(
    monkeypatch, db_engine, seed_royal
):
    stop = asyncio.Event()
    states = [
        [("disk_low", "disk_low:resources", "Disk warning")],
        [("disk_low", "disk_low:resources", "Disk failed")],
        [],
    ]

    def read(_settings):
        value = states.pop(0)
        if not states:
            stop.set()
        return value

    session_factory = sessionmaker(bind=db_engine, future=True)
    service = PushService(session_factory)
    monkeypatch.setattr("robopark_api.services.system_notifications.read_health_alerts", read)
    asyncio.run(
        run_system_notification_loop(
            stop,
            settings=object(),
            emit=service.emit,
            interval_seconds=0,
        )
    )

    with session_factory() as db:
        events = list(db.scalars(select(NotificationEvent).order_by(NotificationEvent.created_at)))
        occurrences = list(db.scalars(select(SystemIncidentOccurrence)))
    assert [event.event_type for event in events] == ["disk_low", "system_recovered"]
    assert len(occurrences) == 1
    assert occurrences[0].resolved_at is not None


def test_health_alert_signature_excludes_severity_but_includes_category():
    warning = health_alerts(
        SystemHealthOut(
            overall="degraded",
            checks=[CheckOut(code="resources", status="warning", message="Disk")],
            update=UpdateOut(),
        )
    )
    failed = health_alerts(
        SystemHealthOut(
            overall="degraded",
            checks=[CheckOut(code="resources", status="failed", message="Disk")],
            update=UpdateOut(),
        )
    )

    assert warning[0][1] == failed[0][1] == "disk_low:resources"


def test_system_notification_loop_retries_failed_emit_without_duplicate_occurrence(
    monkeypatch, db_engine, seed_royal
):
    stop = asyncio.Event()
    down = [("disk_low", "resources:failed", "Диск")]
    states = [down, down]

    def read(_settings):
        value = states.pop(0)
        if not states:
            stop.set()
        return value

    session_factory = sessionmaker(bind=db_engine, future=True)
    service = PushService(session_factory)
    attempts = 0

    def emit_then_fail_once(**event):
        nonlocal attempts
        attempts += 1
        result = service.emit(**event)
        if attempts == 1:
            raise RuntimeError("delivery boundary failed")
        return result

    monkeypatch.setattr("robopark_api.services.system_notifications.read_health_alerts", read)
    asyncio.run(
        run_system_notification_loop(
            stop,
            settings=object(),
            emit=emit_then_fail_once,
            interval_seconds=0,
        )
    )

    with session_factory() as db:
        events = list(db.scalars(select(NotificationEvent)))
        occurrences = list(db.scalars(select(SystemIncidentOccurrence)))
    assert attempts == 2
    assert [event.user_id for event in events] == [seed_royal.id]
    assert len(occurrences) == 1
    assert occurrences[0].resolved_at is None
