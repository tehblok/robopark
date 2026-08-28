from conftest import role_id_for
from robopark_api.services import platform_settings


def test_admin_can_update_screenshot_guard(client, seed_royal, db_session):
    assert (
        client.post("/auth/login", json={"username": "royal", "password": "secret"}).status_code
        == 204
    )

    response = client.put(
        "/admin/settings/screenshot-guard",
        json={"operator": True, "mechanic": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["operator"] is True
    assert payload["mechanic"] is False
    assert payload["admin"] is False
    assert payload["driver"] is False

    assert (
        platform_settings.get_bool_setting(
            db_session,
            platform_settings.SCREENSHOT_GUARD_OPERATOR_KEY,
            False,
        )
        is True
    )


def test_me_reflects_screenshot_guard_for_role(client, seed_royal, db_session):
    platform_settings.set_bool_setting(
        db_session,
        platform_settings.SCREENSHOT_GUARD_OPERATOR_KEY,
        True,
    )

    from robopark_api.models import User
    from robopark_api.security import hash_password

    operator = User(
        username="shot_op",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status="approved",
    )
    db_session.add(operator)
    db_session.commit()

    client.post("/auth/login", json={"username": "shot_op", "password": "secret"})
    me = client.get("/auth/me").json()
    assert me["screenshot_guard"] is True

    client.post("/auth/logout")
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    royal_me = client.get("/auth/me").json()
    assert royal_me["screenshot_guard"] is False
