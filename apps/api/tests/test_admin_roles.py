from conftest import login_as, role_id_for
from robopark_api.models import User
from robopark_api.security import hash_password


def test_list_roles_returns_system_catalog(client, seed_royal):
    login_as(client, "royal", "secret")
    response = client.get("/admin/roles")
    assert response.status_code == 200
    slugs = {row["slug"] for row in response.json()}
    assert {"royal", "admin", "operator", "mechanic", "driver"} <= slugs
    royal = next(row for row in response.json() if row["slug"] == "royal")
    assert "users.approve" in royal["permissions"]
    assert royal["user_count"] >= 1


def test_list_permission_catalog(client, seed_royal):
    login_as(client, "royal", "secret")
    catalog = client.get("/admin/roles/permissions/catalog")
    assert catalog.status_code == 200
    keys = {item["key"] for item in catalog.json()}
    assert "nav.admin" in keys
    assert "users.manage" in keys


def test_cannot_deactivate_system_role(client, seed_royal):
    login_as(client, "royal", "secret")
    roles = client.get("/admin/roles").json()
    admin = next(row for row in roles if row["slug"] == "admin")
    response = client.patch(f"/admin/roles/{admin['id']}", json={"is_active": False})
    assert response.status_code == 400
    assert response.json()["detail"] == "system_role_protected"


def test_admin_cannot_grant_nav_admin_to_operator(client, seed_royal, db_session):
    admin = User(
        username="admin-roles",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "admin"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    login_as(client, "admin-roles", "secret")
    roles = client.get("/admin/roles").json()
    operator = next(row for row in roles if row["slug"] == "operator")
    response = client.patch(
        f"/admin/roles/{operator['id']}",
        json={"permissions": operator["permissions"] + ["nav.admin"]},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "privileged_grant_forbidden"


def test_royal_can_grant_nav_admin_to_operator(client, seed_royal):
    login_as(client, "royal", "secret")
    roles = client.get("/admin/roles").json()
    operator = next(row for row in roles if row["slug"] == "operator")
    response = client.patch(
        f"/admin/roles/{operator['id']}",
        json={"permissions": operator["permissions"] + ["nav.admin"]},
    )
    assert response.status_code == 200
    assert "nav.admin" in response.json()["permissions"]
