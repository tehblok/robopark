from conftest import VALID_PASSWORD


def test_register_creates_pending_operator(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "op1",
            "password": VALID_PASSWORD,
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
        json={"shared_password": "nope", "username": "op1", "password": VALID_PASSWORD},
    )

    assert response.status_code == 403


def test_register_disabled_when_unset(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", None)
    response = client.post(
        "/auth/register",
        json={"shared_password": "x", "username": "op1", "password": VALID_PASSWORD},
    )

    assert response.status_code == 403


def test_register_duplicate_username(client, test_settings, monkeypatch, seed_royal):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "royal",
            "password": VALID_PASSWORD,
        },
    )

    assert response.status_code == 409


def test_register_rejects_weak_password(client, test_settings, monkeypatch):
    """The API used to accept any non-empty password, including a single char."""
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={"shared_password": "gate", "username": "op_weak", "password": "a"},
    )

    assert response.status_code == 422


def test_register_rejects_password_containing_username(client, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    response = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "operator",
            "password": "Operator-12345!",
        },
    )

    assert response.status_code == 422
