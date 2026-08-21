from fastapi.testclient import TestClient


def test_login_me_logout_flow(client: TestClient, seed_royal):
    response = client.post(
        "/auth/login", json={"username": "royal", "password": "secret"}
    )
    assert response.status_code == 204
    assert "robopark_session" in response.cookies

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "id": seed_royal.id,
        "username": "royal",
        "role": "royal",
    }

    logout = client.post("/auth/logout")
    assert logout.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_login_bad_password(client: TestClient, seed_royal):
    response = client.post(
        "/auth/login", json={"username": "royal", "password": "nope"}
    )

    assert response.status_code == 401


def test_me_without_cookie(client: TestClient):
    assert client.get("/auth/me").status_code == 401
