from conftest import VALID_PASSWORD, login_as


def test_admin_creates_and_lists_mechanic(client, seed_royal, seed_park_with_tracker):
    login_as(client, "royal", "secret")
    created = client.post(
        "/admin/mechanics",
        json={
            "username": "mech2",
            "password": VALID_PASSWORD,
            "park_id": seed_park_with_tracker.id,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["username"] == "mech2"
    assert body["is_active"] is True
    assert body["park"]["id"] == seed_park_with_tracker.id

    listed = client.get("/admin/mechanics")
    assert listed.status_code == 200
    assert any(item["username"] == "mech2" for item in listed.json())


def test_duplicate_mechanic_username_conflict(client, seed_royal, seed_mechanic, seed_park_with_tracker):
    login_as(client, "royal", "secret")
    response = client.post(
        "/admin/mechanics",
        json={
            "username": "mech1",
            "password": VALID_PASSWORD,
            "park_id": seed_park_with_tracker.id,
        },
    )
    assert response.status_code == 409


def test_inactive_park_rejected(client, seed_royal, db_session, seed_park_with_tracker):
    seed_park_with_tracker.is_active = False
    db_session.commit()
    login_as(client, "royal", "secret")
    response = client.post(
        "/admin/mechanics",
        json={
            "username": "newmech",
            "password": VALID_PASSWORD,
            "park_id": seed_park_with_tracker.id,
        },
    )
    assert response.status_code == 400


def test_mechanic_password_policy_enforced(client, seed_royal, seed_park_with_tracker):
    """Admin-created accounts must satisfy the same policy as self-registration."""
    response = client.post(
        "/admin/mechanics",
        json={
            "username": "weakmech",
            "password": "123",
            "park_id": seed_park_with_tracker.id,
        },
    )
    assert response.status_code in (401, 422)

    login_as(client, "royal", "secret")
    response = client.post(
        "/admin/mechanics",
        json={
            "username": "weakmech",
            "password": "123",
            "park_id": seed_park_with_tracker.id,
        },
    )
    assert response.status_code == 422


def test_deactivating_mechanic_revokes_sessions(
    client, db_session, seed_royal, seed_mechanic
):
    """A disabled account must lose access immediately, not at cookie expiry."""
    from robopark_api.models import AuthSession

    login_as(client, "mech1", "secret")
    assert client.get("/auth/me").status_code == 200
    assert db_session.query(AuthSession).filter_by(user_id=seed_mechanic.id).count() == 1

    admin_client_cookies = dict(client.cookies)
    client.cookies.clear()
    login_as(client, "royal", "secret")
    patched = client.patch(
        f"/admin/mechanics/{seed_mechanic.id}", json={"is_active": False}
    )
    assert patched.status_code == 200
    assert db_session.query(AuthSession).filter_by(user_id=seed_mechanic.id).count() == 0

    client.cookies.clear()
    client.cookies.update(admin_client_cookies)
    assert client.get("/auth/me").status_code == 401


def test_admin_updates_mechanic_tracker_login(client, seed_royal, seed_mechanic):
    login_as(client, "royal", "secret")
    response = client.patch(
        f"/admin/mechanics/{seed_mechanic.id}",
        json={"tracker_login": "mech1-startrek"},
    )
    assert response.status_code == 200
    assert response.json()["tracker_login"] == "mech1-startrek"

    me = client.get("/auth/me")
    assert me.status_code == 200

    login_as(client, "mech1", "secret")
    assert client.get("/auth/me").json()["tracker_login"] == "mech1-startrek"
