import pytest
from fastapi.testclient import TestClient

from conftest import VALID_PASSWORD, login_as, role_id_for
from park_helpers import PARK_DEFAULTS
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password


def test_admin_creates_and_lists_park(client: TestClient, seed_royal):
    login_as(client, "royal", "secret")

    created = client.post("/parks", json={"name": "Next", "tag": "Next"})

    assert created.status_code == 201
    assert created.json() == {
        "id": created.json()["id"],
        "name": "Next",
        "tag": "Next",
        "is_active": True,
        **PARK_DEFAULTS,
    }
    listed = client.get("/parks")
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_park_persists_its_iana_timezone_for_sla(client: TestClient, seed_royal):
    login_as(client, "royal", "secret")

    created = client.post(
        "/parks",
        json={"name": "East", "tag": "East", "timezone": "Asia/Yekaterinburg"},
    )

    assert created.status_code == 201
    assert created.json()["timezone"] == "Asia/Yekaterinburg"
    assert client.get("/parks").json()[0]["timezone"] == "Asia/Yekaterinburg"

    updated = client.patch(
        f"/parks/{created.json()['id']}",
        json={"timezone": "Europe/Berlin"},
    )
    assert updated.status_code == 200
    assert updated.json()["timezone"] == "Europe/Berlin"

    invalid = client.patch(
        f"/parks/{created.json()['id']}",
        json={"timezone": "Invalid/Zone"},
    )
    assert invalid.status_code == 422


def test_park_api_does_not_expose_or_store_removed_coordinates(
    client: TestClient, seed_royal, db_session
):
    login_as(client, "royal", "secret")
    created = client.post(
        "/parks", json={"name": "Север", "tag": "north", "latitude": 55.75, "longitude": 37.62}
    )
    assert created.status_code == 201
    park_id = created.json()["id"]
    assert "latitude" not in created.json()
    assert "longitude" not in created.json()
    assert "latitude" not in client.get("/parks").json()[0]
    persisted = db_session.get(Park, park_id)
    assert persisted is not None
    assert persisted.latitude is None
    assert persisted.longitude is None


def test_operator_cannot_create_park(client: TestClient, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "op1",
            "password": VALID_PASSWORD,
        },
    )
    login_as(client, "op1", VALID_PASSWORD)

    assert client.post("/parks", json={"name": "X", "tag": "x"}).status_code == 403


@pytest.mark.parametrize("role", ["driver", "operator", "mechanic"])
def test_non_manager_sees_only_assigned_active_parks(client: TestClient, db_session, role: str):
    reader = User(
        username=f"map-{role}",
        password_hash=hash_password(VALID_PASSWORD),
        role_id=role_id_for(db_session, role),
        access_status="approved",
        is_active=True,
    )
    assigned = Park(name="Assigned", tag="assigned", is_active=True)
    foreign = Park(name="Foreign", tag="foreign", is_active=True)
    inactive = Park(name="Inactive", tag="inactive", is_active=False)
    db_session.add_all([reader, assigned, foreign, inactive])
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=reader.id, park_id=assigned.id),
            UserPark(user_id=reader.id, park_id=inactive.id),
        ]
    )
    db_session.commit()
    login_as(client, f"map-{role}", VALID_PASSWORD)

    response = client.get("/parks")
    assert response.status_code == 200
    assert [park["id"] for park in response.json()] == [assigned.id]


def test_admin_updates_park_and_lists_inactive(client: TestClient, seed_royal):
    login_as(client, "royal", "secret")
    park_id = client.post("/parks", json={"name": "Old", "tag": "old"}).json()["id"]

    updated = client.patch(
        f"/parks/{park_id}",
        json={"name": "New", "tag": "new", "is_active": False},
    )

    assert updated.status_code == 200
    assert updated.json() == {
        "id": park_id,
        "name": "New",
        "tag": "new",
        "is_active": False,
        **PARK_DEFAULTS,
    }
    assert client.get("/parks").json() == [updated.json()]


def test_duplicate_park_tag_returns_conflict(client: TestClient, seed_royal):
    login_as(client, "royal", "secret")
    client.post("/parks", json={"name": "One", "tag": "same"})

    duplicate = client.post("/parks", json={"name": "Two", "tag": "same"})

    assert duplicate.status_code == 409


def test_update_to_duplicate_tag_returns_conflict(client: TestClient, seed_royal):
    login_as(client, "royal", "secret")
    client.post("/parks", json={"name": "One", "tag": "one"})
    park_id = client.post("/parks", json={"name": "Two", "tag": "two"}).json()["id"]

    duplicate = client.patch(f"/parks/{park_id}", json={"tag": "one"})

    assert duplicate.status_code == 409


def test_update_missing_park_returns_not_found(client: TestClient, seed_royal):
    login_as(client, "royal", "secret")

    assert client.patch("/parks/999", json={"name": "Missing"}).status_code == 404
