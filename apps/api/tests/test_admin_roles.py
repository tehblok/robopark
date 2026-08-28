from conftest import login_as


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
