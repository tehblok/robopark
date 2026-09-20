import asyncio

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from robopark_api.ops_schemas import CheckOut, SystemHealthOut, UpdateOut
from robopark_api.routers.push import PushService
from robopark_api.schedule_models import NotificationEvent, SystemIncidentOccurrence
from robopark_api.services.system_notifications import health_alerts, run_system_notification_loop


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
        "integration_down",
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
    assert [event.user_id for event in events] == [seed_royal.id, seed_royal.id]
    assert len(occurrences) == 2
    assert occurrences[0].resolved_at is not None
    assert occurrences[1].resolved_at is None
