from robopark_api.services import platform_settings


def test_admin_can_update_tracker_policy(client, seed_royal, db_session):
    assert client.post("/auth/login", json={"username": "royal", "password": "secret"}).status_code == 204

    response = client.put(
        "/admin/settings/tracker-policy",
        json={"operator_show_untagged": False, "mechanic_can_write": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_show_untagged"] is False
    assert payload["mechanic_can_write"] is False

    assert platform_settings.get_bool_setting(
        db_session,
        platform_settings.TRACKER_OPERATOR_UNTAGGED_KEY,
        True,
    ) is False
