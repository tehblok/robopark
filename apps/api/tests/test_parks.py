from fastapi.testclient import TestClient

from conftest import login_as

from park_helpers import PARK_DEFAULTS


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


def test_operator_cannot_create_park(
    client: TestClient, test_settings, monkeypatch
):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "op1",
            "password": "secret1",
        },
    )
    login_as(client, "op1", "secret1")

    assert client.post("/parks", json={"name": "X", "tag": "x"}).status_code == 403


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
