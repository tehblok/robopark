from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, User
from robopark_api.security import hash_password
from robopark_api.services import (
    emergency_bridge_client,
    emergency_client,
    emergency_config,
    emergency_scope,
    platform_settings,
)


def test_snapshot_uses_bridge_without_server_cookie(client, db_session, test_settings, monkeypatch):
    user = User(
        username="bridge-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    emergency_config.seed_emergency_config(db_session, emergency_config.DEFAULT_JSON_PATH)
    test_settings.emergency_bridge_enabled = True
    test_settings.emergency_direct_fallback_enabled = False
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)
    monkeypatch.setattr(
        emergency_bridge_client,
        "fetch_robot_payload",
        lambda **kwargs: {"vin": kwargs["vin"], "isOnline": True},
    )
    login_as(client, "bridge-operator", "secret")

    response = client.get("/emergency/447/snapshot")

    assert response.status_code == 200
    assert response.json()["vin"] == "YASADR00000000447"


def test_healthy_server_cookie_is_primary_and_does_not_call_mac(
    client, db_session, test_settings, monkeypatch
):
    user = User(
        username="bridge-no-service-fallback",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    emergency_config.seed_emergency_config(db_session, emergency_config.DEFAULT_JSON_PATH)
    test_settings.emergency_bridge_enabled = True
    test_settings.emergency_direct_fallback_enabled = True
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)
    monkeypatch.setattr(
        emergency_bridge_client,
        "fetch_robot_payload",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected Mac fallback")),
    )
    monkeypatch.setattr(
        platform_settings,
        "get_emergency_cookie_probe",
        lambda *_args: ("Session_id=server", "server-cookie"),
    )
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **kwargs: {"vin": kwargs["vin"], "isOnline": True},
    )
    login_as(client, user.username, "secret")

    response = client.get("/emergency/447/snapshot")

    assert response.status_code == 200


def test_server_auth_denial_never_falls_back_to_owner_mac(
    client, db_session, test_settings, monkeypatch
):
    user = User(
        username="bridge-auth-denied",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    emergency_config.seed_emergency_config(db_session, emergency_config.DEFAULT_JSON_PATH)
    test_settings.emergency_bridge_enabled = True
    test_settings.emergency_direct_fallback_enabled = True
    monkeypatch.setattr(emergency_scope, "vin_allowed_for_user", lambda *_args: True)
    monkeypatch.setattr(
        platform_settings,
        "get_emergency_cookie_probe",
        lambda *_args: ("Session_id=server", "server-cookie"),
    )
    monkeypatch.setattr(
        emergency_bridge_client,
        "fetch_robot_payload",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("authorization bypass")),
    )
    monkeypatch.setattr(
        emergency_client,
        "fetch_robot_payload",
        lambda **_kwargs: (_ for _ in ()).throw(
            emergency_client.EmergencyAuthError("server cookie denied")
        ),
    )
    login_as(client, user.username, "secret")

    response = client.get("/emergency/447/snapshot")

    assert response.status_code == 403
