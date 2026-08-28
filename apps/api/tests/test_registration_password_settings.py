from conftest import VALID_PASSWORD, role_id_for
from robopark_api.models import User
from robopark_api.security import hash_password
from robopark_api.services import platform_settings


def test_royal_can_set_registration_password(client, seed_royal, db_session):
    client.post("/auth/login", json={"username": "royal", "password": "secret"})

    response = client.put(
        "/admin/settings/registration-password",
        json={"password": "club-gate"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["password_masked"] is not None
    assert platform_settings.get_registration_shared_password(db_session) == "club-gate"


def test_admin_cannot_manage_registration_password(client, seed_royal, db_session):
    admin = User(
        username="admin1",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "admin"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()

    client.post("/auth/login", json={"username": "admin1", "password": "secret"})
    assert client.get("/admin/settings/registration-password").status_code == 403
    assert (
        client.put(
            "/admin/settings/registration-password",
            json={"password": "nope"},
        ).status_code
        == 403
    )


def test_register_uses_db_shared_password(
    client, test_settings, seed_royal, db_session, monkeypatch
):
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    client.put("/admin/settings/registration-password", json={"password": "db-gate"})
    client.post("/auth/logout")

    monkeypatch.setattr(test_settings, "operator_shared_password", "env-gate")

    response = client.post(
        "/auth/register",
        json={
            "shared_password": "db-gate",
            "username": "new_op",
            "password": VALID_PASSWORD,
            "role_slug": "operator",
        },
    )
    assert response.status_code == 201

    client.post("/auth/logout")
    wrong = client.post(
        "/auth/register",
        json={
            "shared_password": "env-gate",
            "username": "new_op2",
            "password": VALID_PASSWORD,
            "role_slug": "operator",
        },
    )
    assert wrong.status_code == 403


def test_clear_registration_password_closes_register(client, test_settings, seed_royal):
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    client.put("/admin/settings/registration-password", json={"password": "gate"})
    client.post("/auth/logout")

    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    cleared = client.delete("/admin/settings/registration-password")
    assert cleared.status_code == 200
    assert cleared.json()["configured"] is False
    client.post("/auth/logout")

    test_settings.operator_shared_password = None
    response = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "blocked",
            "password": VALID_PASSWORD,
            "role_slug": "operator",
        },
    )
    assert response.status_code == 403
