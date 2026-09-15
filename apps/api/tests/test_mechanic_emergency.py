import json
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import login_as
from robopark_api.models import EmergencyReading
from robopark_api.services import emergency_cache, emergency_config, emergency_scope
from robopark_api.services import platform_settings as settings_svc

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def empty_emergency_cache():
    emergency_cache.clear_cache_for_tests()
    yield
    emergency_cache.clear_cache_for_tests()


@pytest.fixture(autouse=True)
def allow_vin_scope(monkeypatch):
    """Focus these tests on cookie / payload behavior, not VIN park-scope."""
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_a, **_k: True)


def test_emergency_resolve(client, db_session, seed_mechanic, seed_royal):
    emergency_config.seed_emergency_config(db_session, emergency_config.DEFAULT_JSON_PATH)
    payload = json.loads((FIXTURES / "emergency_robot.json").read_text(encoding="utf-8"))
    login_as(client, "royal", "secret")
    with patch(
        "robopark_api.services.emergency_client.fetch_robot_payload",
        return_value=payload,
    ):
        setup = client.put(
            "/admin/settings/emergency-cookie",
            json={"cookie": "Session_id=test", "robot_number": "447"},
        )
        assert setup.status_code == 200
        login_as(client, "mech1", "secret")
        response = client.post("/mechanic/emergency/resolve", json={"robot_number": "447"})

    assert response.status_code == 200
    body = response.json()
    assert body["vin"] == "YASADR00000000447"
    assert body["sections"]


def test_emergency_invalid_cookie(client, seed_mechanic, seed_royal):
    login_as(client, "royal", "secret")
    from robopark_api.services.emergency_client import EmergencyAuthError

    with patch(
        "robopark_api.services.emergency_client.fetch_robot_payload",
        side_effect=EmergencyAuthError("invalid"),
    ):
        setup = client.put(
            "/admin/settings/emergency-cookie",
            json={"cookie": "bad", "robot_number": "447"},
        )
        assert setup.status_code == 200
        login_as(client, "mech1", "secret")
        response = client.post("/mechanic/emergency/resolve", json={"robot_number": "447"})

    assert response.status_code == 403
    assert response.json()["detail"] == "emergency_cookie_invalid"


def test_mechanic_snapshot_excludes_operator_only_readings(client, db_session, seed_mechanic):
    emergency_config.seed_emergency_config(db_session, emergency_config.DEFAULT_JSON_PATH)
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "Session_id=test")
    visible = EmergencyReading(
        section_id="status",
        path="isOnline",
        label="Online",
        display_kind="state",
        unit=None,
        precision=0,
        enabled_path=None,
        no_data_json="[]",
        view="front",
        x=0.2,
        y=0.4,
        label_direction="right",
        sort_order=1,
    )
    hidden = EmergencyReading(
        section_id="position_route",
        path="position.lat",
        label="Latitude",
        display_kind="number",
        unit=None,
        precision=1,
        enabled_path=None,
        no_data_json="[]",
        view="top",
        x=0.7,
        y=0.4,
        label_direction="left",
        sort_order=0,
    )
    db_session.add_all([visible, hidden])
    db_session.commit()
    payload = {"isOnline": True, "position": {"lat": 55.7}}
    login_as(client, "mech1", "secret")

    with patch(
        "robopark_api.services.emergency_client.fetch_robot_payload",
        return_value=payload,
    ):
        response = client.get("/emergency/447/snapshot")

    assert response.status_code == 200
    assert [(item["id"], item["section_id"]) for item in response.json()["readings"]] == [
        (visible.id, "status")
    ]
