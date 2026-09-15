from unittest.mock import patch

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, EmergencyReading, User
from robopark_api.security import hash_password
from robopark_api.services import emergency_config
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.rbac import RoleSlug


def test_driver_emergency_skips_blocker_scope(client, db_session, monkeypatch):
    from robopark_api.services import emergency_scope

    driver = User(
        username="driver_scope",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, RoleSlug.DRIVER),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)

    def _fail_if_called(*_args, **_kwargs):
        raise AssertionError("tracker should not be queried for driver emergency scope")

    monkeypatch.setattr(emergency_scope.tracker_cache, "search_robot_tickets", _fail_if_called)
    assert emergency_scope.vin_allowed_for_user(db_session, driver, "YASADR00000000447") is True


def test_pending_driver_cannot_use_emergency(client, db_session):
    driver = User(
        username="driver_pending",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, RoleSlug.DRIVER),
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db_session.add(driver)
    db_session.commit()

    assert (
        client.post(
            "/auth/login", json={"username": "driver_pending", "password": "secret"}
        ).status_code
        == 204
    )
    response = client.post("/emergency/resolve", json={"robot_number": "447"})
    assert response.status_code == 403


def test_driver_snapshot_excludes_operator_only_readings(client, db_session):
    driver = User(
        username="driver_readings",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, RoleSlug.DRIVER),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(driver)
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
    login_as(client, "driver_readings", "secret")

    with patch(
        "robopark_api.services.emergency_client.fetch_robot_payload",
        return_value={"isOnline": True, "position": {"lat": 55.7}},
    ):
        response = client.get("/emergency/447/snapshot")

    assert response.status_code == 200
    assert [(item["id"], item["section_id"]) for item in response.json()["readings"]] == [
        (visible.id, "status")
    ]
