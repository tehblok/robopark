def test_register_creates_pending_operator(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "op1",
            "password": "secret1",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "op1"
    assert body["role"] == "operator"
    assert body["access_status"] == "pending"
    assert "robopark_session" not in response.cookies


def test_register_wrong_shared_password(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={"shared_password": "nope", "username": "op1", "password": "secret1"},
    )

    assert response.status_code == 403


def test_register_disabled_when_unset(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", None)
    response = client.post(
        "/auth/register",
        json={"shared_password": "x", "username": "op1", "password": "secret1"},
    )

    assert response.status_code == 403


def test_register_duplicate_username(client, test_settings, monkeypatch, seed_royal):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={"shared_password": "gate", "username": "royal", "password": "secret1"},
    )

    assert response.status_code == 409
