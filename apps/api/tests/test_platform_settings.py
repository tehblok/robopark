from conftest import login_as


def test_get_integrations_empty(client, seed_royal):
    login_as(client, "royal", "secret")
    response = client.get("/admin/settings/integrations")
    assert response.status_code == 200
    body = response.json()
    assert body["tracker_token_masked"] is None
    assert body["emergency_cookie_masked"] is None


def test_set_tracker_token_masked(client, seed_royal):
    login_as(client, "royal", "secret")
    put = client.put(
        "/admin/settings/tracker-token",
        json={"token": "oauth-secret-token"},
    )
    assert put.status_code == 200
    body = put.json()
    assert body["tracker_token_masked"] == f"•••• ({len('oauth-secret-token')})"
    assert "oauth-secret-token" not in str(body)
    assert "oken" not in body["tracker_token_masked"]


def test_set_emergency_cookie(client, seed_royal):
    login_as(client, "royal", "secret")
    put = client.put(
        "/admin/settings/emergency-cookie",
        json={"cookie": "Session_id=abc123"},
    )
    assert put.status_code == 200
    body = put.json()
    assert body["emergency_cookie_valid"] is True
    assert "Session_id" not in body["emergency_cookie_masked"]
