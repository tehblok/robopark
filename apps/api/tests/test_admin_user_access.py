from conftest import VALID_PASSWORD, login_as, role_id_for
from robopark_api.models import User
from robopark_api.security import hash_password


def test_individual_permissions_override_role(client, seed_royal, seed_mechanic):
    login_as(client, "royal", "secret")
    patched = client.patch(
        f"/admin/users/{seed_mechanic.id}",
        json={
            "permissions": ["nav.emergency", "nav.dashboard", "nav.analytics"],
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert "nav.analytics" in body["permissions"]
    assert "nav.emergency" in body["permissions"]
    assert "nav.tasks" not in body["permissions"]

    login_as(client, "mech1", "secret")
    me = client.get("/auth/me").json()
    assert "nav.analytics" in me["permissions"]
    assert "nav.tasks" not in me["permissions"]


def test_delete_user(client, seed_royal, db_session):
    extra = User(
        username="to-delete",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(extra)
    db_session.commit()
    db_session.refresh(extra)

    login_as(client, "royal", "secret")
    assert client.delete(f"/admin/users/{extra.id}").status_code == 204
    listed = client.get("/admin/users").json()
    assert all(row["username"] != "to-delete" for row in listed)


def test_cannot_delete_self(client, seed_royal):
    login_as(client, "royal", "secret")
    response = client.delete(f"/admin/users/{seed_royal.id}")
    assert response.status_code == 400
    assert response.json()["detail"] == "cannot_delete_self"


def test_cannot_delete_last_royal(client, seed_royal, db_session):
    other = User(
        username="royal-two",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "royal"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    login_as(client, "royal", "secret")
    assert client.delete(f"/admin/users/{other.id}").status_code == 204
    leftover = [row for row in client.get("/admin/users").json() if row["role"] == "royal"]
    assert len(leftover) == 1
    assert client.delete(f"/admin/users/{leftover[0]['id']}").status_code == 400


def test_cannot_deactivate_or_demote_last_royal(client, seed_royal):
    login_as(client, "royal", "secret")
    deactivated = client.patch(f"/admin/users/{seed_royal.id}", json={"is_active": False})
    assert deactivated.status_code == 400
    assert deactivated.json()["detail"] == "cannot_disable_last_royal"

    demoted = client.patch(f"/admin/users/{seed_royal.id}", json={"role_slug": "admin"})
    assert demoted.status_code == 400
    assert demoted.json()["detail"] == "cannot_disable_last_royal"


def test_admin_cannot_change_access_status(client, seed_royal, seed_pending_operator, db_session):
    admin = User(
        username="admin-user",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "admin"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()

    login_as(client, "admin-user", "secret")
    response = client.patch(
        f"/admin/users/{seed_pending_operator.id}",
        json={"access_status": "approved"},
    )
    assert response.status_code == 403
    db_session.refresh(seed_pending_operator)
    assert seed_pending_operator.access_status == "pending"

