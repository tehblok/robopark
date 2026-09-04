from fastapi.testclient import TestClient

from conftest import VALID_PASSWORD, login_as


def register_user(
    client: TestClient,
    test_settings,
    monkeypatch,
    username: str,
    *,
    role_slug: str = "operator",
) -> int:
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": username,
            "password": VALID_PASSWORD,
            "role_slug": role_slug,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_approve_requires_parks_and_sets_approved(
    client: TestClient,
    seed_royal,
    test_settings,
    monkeypatch,
):
    login_as(client, "royal", "secret")
    park_id = client.post("/parks", json={"name": "Next", "tag": "Next"}).json()["id"]
    client.post("/auth/logout")
    operator_id = register_user(client, test_settings, monkeypatch, "op1")
    login_as(client, "royal", "secret")

    inbox = client.get("/admin/users?access_status=pending")
    assert inbox.status_code == 200
    assert any(user["id"] == operator_id and user["username"] == "op1" for user in inbox.json())
    assert (
        client.post(
            f"/admin/users/{operator_id}/approve",
            json={"park_ids": [park_id]},
        ).status_code
        == 204
    )

    client.post("/auth/logout")
    login_as(client, "op1", VALID_PASSWORD)
    me = client.get("/auth/me").json()
    assert me["access_status"] == "approved"
    assert any(park["id"] == park_id for park in me["parks"])


def test_reject_sets_rejected(
    client: TestClient,
    seed_royal,
    test_settings,
    monkeypatch,
):
    operator_id = register_user(client, test_settings, monkeypatch, "op2")
    login_as(client, "royal", "secret")

    assert client.post(f"/admin/users/{operator_id}/reject").status_code == 204

    client.post("/auth/logout")
    login_as(client, "op2", VALID_PASSWORD)
    assert client.get("/auth/me").json()["access_status"] == "rejected"


def test_approve_rejects_inactive_parks(
    client: TestClient,
    seed_royal,
    test_settings,
    monkeypatch,
):
    operator_id = register_user(client, test_settings, monkeypatch, "op3")
    login_as(client, "royal", "secret")
    park_id = client.post("/parks", json={"name": "Old", "tag": "old"}).json()["id"]
    client.patch(f"/parks/{park_id}", json={"is_active": False})

    response = client.post(
        f"/admin/users/{operator_id}/approve",
        json={"park_ids": [park_id]},
    )

    assert response.status_code == 400


def test_approve_rejects_non_pending_user(
    client: TestClient,
    seed_royal,
    test_settings,
    monkeypatch,
):
    operator_id = register_user(client, test_settings, monkeypatch, "op4")
    login_as(client, "royal", "secret")
    park_id = client.post("/parks", json={"name": "Next", "tag": "next"}).json()["id"]
    assert (
        client.post(
            f"/admin/users/{operator_id}/approve",
            json={"park_ids": [park_id]},
        ).status_code
        == 204
    )

    response = client.post(
        f"/admin/users/{operator_id}/approve",
        json={"park_ids": [park_id]},
    )

    assert response.status_code == 400


def test_driver_registration_gets_overview_robot_and_check_permissions(
    client: TestClient,
    seed_royal,
    test_settings,
    monkeypatch,
):
    user_id = register_user(client, test_settings, monkeypatch, "driver1", role_slug="driver")
    login_as(client, "royal", "secret")
    assert client.post(f"/admin/users/{user_id}/approve", json={"park_ids": []}).status_code == 204

    client.post("/auth/logout")
    login_as(client, "driver1", VALID_PASSWORD)
    permissions = set(client.get("/auth/me").json()["permissions"])

    assert {
        "nav.dashboard",
        "nav.tasks",
        "nav.robot_search",
        "nav.emergency",
        "nav.reports",
        "tracker.read",
        "reports.create",
    } <= permissions
    assert {
        "tracker.write",
        "tracker.attach",
        "reports.resolve",
    }.isdisjoint(permissions)
