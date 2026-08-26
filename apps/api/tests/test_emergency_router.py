import json
from pathlib import Path

import pytest

from conftest import login_as
from robopark_api.models import AccessStatus, User, UserPark, UserRole
from robopark_api.security import hash_password
from robopark_api.services import (
    emergency_cache,
    emergency_client,
    emergency_config,
    emergency_scope,
)
from robopark_api.services import platform_settings as settings_svc

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def empty_emergency_cache():
    emergency_cache.clear_cache_for_tests()
    yield
    emergency_cache.clear_cache_for_tests()


@pytest.fixture
def seed_operator(db_session, seed_park_with_tracker):
    user = User(
        username="operator1",
        password_hash=hash_password("secret"),
        role=UserRole.operator.value,
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def emergency_payload():
    return json.loads((FIXTURES / "emergency_robot.json").read_text(encoding="utf-8"))


def configure_emergency(db_session, monkeypatch, emergency_payload, *, allow_vin: bool = True):
    """Set up an Emergency happy-path environment.

    The VIN park-scope ACL is orthogonal to what most of these tests want to
    assert (section visibility, VIN normalization, cookie state), so we bypass
    it here. The ACL itself has focused unit tests in ``test_emergency_scope``
    and wire-up tests in ``test_emergency_router::test_operator_out_of_scope*``.
    """
    emergency_config.seed_emergency_config(db_session, emergency_config.DEFAULT_JSON_PATH)
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "Session_id=test")
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **_kwargs: emergency_payload,
    )
    if allow_vin:
        monkeypatch.setattr(
            emergency_scope, "vin_allowed_for_user", lambda *_args, **_kwargs: True
        )


def test_operator_resolve_hides_service_raw(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload)
    login_as(client, "operator1", "secret")

    response = client.post("/emergency/resolve", json={"robot_number": "447"})

    assert response.status_code == 200
    section_ids = [section["id"] for section in response.json()["sections"]]
    assert "status" in section_ids
    assert "service_raw" not in section_ids


def test_operator_cannot_open_hidden_section(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload)
    login_as(client, "operator1", "secret")

    response = client.get("/emergency/YASADR00000000447/sections/service_raw")

    assert response.status_code == 404


def test_mechanic_cannot_open_service_raw(
    client, db_session, seed_mechanic, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload)
    login_as(client, "mech1", "secret")

    response = client.get("/emergency/YASADR00000000447/sections/service_raw")

    assert response.status_code == 404


def test_pending_operator_cannot_use_emergency(
    client, db_session, seed_pending_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload)
    login_as(client, "operator", "secret")

    response = client.post("/emergency/resolve", json={"robot_number": "447"})

    assert response.status_code == 403


def test_section_path_normalizes_vin(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload)
    login_as(client, "operator1", "secret")

    response = client.get("/emergency/447/sections/status")

    assert response.status_code == 200
    assert response.json()["id"] == "status"


def test_section_path_rejects_invalid_vin(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload)
    login_as(client, "operator1", "secret")

    response = client.get("/emergency/not-a-robot/sections/status")

    assert response.status_code == 400


def test_mechanic_alias_still_works(
    client, db_session, seed_mechanic, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload)
    login_as(client, "mech1", "secret")

    response = client.post("/mechanic/emergency/resolve", json={"robot_number": "447"})

    assert response.status_code == 200
    assert response.json()["vin"] == "YASADR00000000447"


def test_operator_out_of_scope_resolve_returns_403(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    """Resolve must reject a VIN that has no Tracker ticket in the user's park."""
    configure_emergency(db_session, monkeypatch, emergency_payload, allow_vin=False)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_a, **_k: False)
    login_as(client, "operator1", "secret")

    response = client.post("/emergency/resolve", json={"robot_number": "447"})

    assert response.status_code == 403
    assert response.json()["detail"] == "emergency_vin_out_of_scope"


def test_operator_out_of_scope_snapshot_returns_403(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    """The snapshot endpoint enforces the same VIN scope as resolve/sections."""
    configure_emergency(db_session, monkeypatch, emergency_payload, allow_vin=False)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_a, **_k: False)
    login_as(client, "operator1", "secret")

    response = client.get("/emergency/YASADR00000000447/snapshot")

    assert response.status_code == 403
    assert response.json()["detail"] == "emergency_vin_out_of_scope"


def test_operator_out_of_scope_section_returns_403(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload, allow_vin=False)
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_a, **_k: False)
    login_as(client, "operator1", "secret")

    response = client.get("/emergency/YASADR00000000447/sections/status")

    assert response.status_code == 403
    assert response.json()["detail"] == "emergency_vin_out_of_scope"


def test_snapshot_returns_hud_when_allowed(
    client, db_session, seed_operator, monkeypatch, emergency_payload
):
    configure_emergency(db_session, monkeypatch, emergency_payload)
    login_as(client, "operator1", "secret")

    response = client.get("/emergency/447/snapshot")

    assert response.status_code == 200
    body = response.json()
    assert body["vin"] == "YASADR00000000447"
    assert body["short_number"] == "447"
