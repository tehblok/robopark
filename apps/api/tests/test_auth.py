from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import AuthSession
from robopark_api.security import hash_session_token


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


def test_login_cookie_is_httponly_and_samesite_lax(
    client: TestClient, seed_royal
):
    response = client.post(
        "/auth/login", json={"username": "royal", "password": "secret"}
    )

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def test_login_stores_only_session_token_hash(
    client: TestClient, db_session: Session, seed_royal
):
    response = client.post(
        "/auth/login", json={"username": "royal", "password": "secret"}
    )
    raw_token = response.cookies["robopark_session"]
    auth_session = db_session.scalar(select(AuthSession))

    assert auth_session is not None
    assert auth_session.token_hash != raw_token
    assert auth_session.token_hash == hash_session_token(raw_token)


def test_logout_deletes_auth_session(
    client: TestClient, db_session: Session, seed_royal
):
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    assert db_session.scalar(select(AuthSession)) is not None

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert db_session.scalar(select(AuthSession)) is None


def test_inactive_user_cannot_login(
    client: TestClient, db_session: Session, seed_royal
):
    seed_royal.is_active = False
    db_session.commit()

    response = client.post(
        "/auth/login", json={"username": "royal", "password": "secret"}
    )

    assert response.status_code == 401
    assert db_session.scalar(select(AuthSession)) is None


def test_expired_session_cannot_access_me(
    client: TestClient, db_session: Session, seed_royal
):
    raw_token = "expired-session-token"
    db_session.add(
        AuthSession(
            user_id=seed_royal.id,
            token_hash=hash_session_token(raw_token),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    db_session.commit()
    client.cookies.set("robopark_session", raw_token)

    assert client.get("/auth/me").status_code == 401


def test_me_without_cookie(client: TestClient):
    assert client.get("/auth/me").status_code == 401
