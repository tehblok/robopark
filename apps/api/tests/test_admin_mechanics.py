from conftest import login_as

from park_helpers import PARK_DEFAULTS


def test_admin_creates_and_lists_mechanic(client, seed_royal, seed_park_with_tracker):
    login_as(client, "royal", "secret")
    created = client.post(
        "/admin/mechanics",
        json={
            "username": "mech2",
            "password": "secret2",
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
            "password": "other",
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
        json={"username": "newmech", "password": "secret", "park_id": seed_park_with_tracker.id},
    )
    assert response.status_code == 400
