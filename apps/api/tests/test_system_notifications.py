import asyncio

from robopark_api.ops_schemas import CheckOut, SystemHealthOut, UpdateOut
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


def test_system_notification_loop_emits_once_until_condition_clears(monkeypatch):
    stop = asyncio.Event()
    states = [
        [("server_problem", "system:degraded", "Система требует внимания")],
        [("server_problem", "system:degraded", "Система требует внимания")],
        [],
        [("server_problem", "system:degraded", "Система требует внимания")],
    ]
    emitted = []

    def read(_settings):
        value = states.pop(0)
        if not states:
            stop.set()
        return value

    monkeypatch.setattr("robopark_api.services.system_notifications.read_health_alerts", read)
    asyncio.run(
        run_system_notification_loop(
            stop,
            settings=object(),
            emit=lambda **kwargs: emitted.append(kwargs),
            interval_seconds=0,
        )
    )
    assert len(emitted) == 2
    assert emitted[0]["event_key"] == "system:system:degraded"
