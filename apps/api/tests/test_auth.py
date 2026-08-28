from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from park_helpers import PARK_DEFAULTS
from robopark_api.deps import require_admin, require_approved_operator
from robopark_api.models import AuthSession, Park, User, UserPark
from robopark_api.security import hash_session_token
from robopark_api.services.rbac import ALL_PERMISSIONS


def test_login_me_logout_flow(client: TestClient, seed_royal):
    response = login_as(client, "royal", "secret")
    assert response.status_code == 204
    assert "robopark_session" in response.cookies

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "id": seed_royal.id,
        "username": "royal",
        "role": "royal",
        "access_status": "approved",
        "tracker_login": None,
        "must_change_password": False,
        "screenshot_guard": False,
        "permissions": sorted(ALL_PERMISSIONS),
        "parks": [],
    }

    logout = client.post("/auth/logout")
    assert logout.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_login_bad_password(client: TestClient, seed_royal):
    response = client.post("/auth/login", json={"username": "royal", "password": "nope"})

    assert response.status_code == 401


def test_login_cookie_is_httponly_and_samesite_lax(client: TestClient, seed_royal):
    response = client.post("/auth/login", json={"username": "royal", "password": "secret"})

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie


def test_login_stores_only_session_token_hash(client: TestClient, db_session: Session, seed_royal):
    response = client.post("/auth/login", json={"username": "royal", "password": "secret"})
    raw_token = response.cookies["robopark_session"]
    auth_session = db_session.scalar(select(AuthSession))

    assert auth_session is not None
    assert auth_session.token_hash != raw_token
    assert auth_session.token_hash == hash_session_token(raw_token)


def test_logout_deletes_auth_session(client: TestClient, db_session: Session, seed_royal):
    client.post("/auth/login", json={"username": "royal", "password": "secret"})
    assert db_session.scalar(select(AuthSession)) is not None

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert db_session.scalar(select(AuthSession)) is None


def test_inactive_user_cannot_login(client: TestClient, db_session: Session, seed_royal):
    seed_royal.is_active = False
    db_session.commit()

    response = client.post("/auth/login", json={"username": "royal", "password": "secret"})

    assert response.status_code == 401
    assert db_session.scalar(select(AuthSession)) is None


def test_expired_session_cannot_access_me(client: TestClient, db_session: Session, seed_royal):
    raw_token = "expired-session-token"
    db_session.add(
        AuthSession(
            user_id=seed_royal.id,
            token_hash=hash_session_token(raw_token),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    db_session.commit()
    client.cookies.set("robopark_session", raw_token)

    assert client.get("/auth/me").status_code == 401


def test_me_without_cookie(client: TestClient):
    assert client.get("/auth/me").status_code == 401


def test_me_includes_assigned_parks(client: TestClient, db_session: Session, seed_royal):
    park = Park(name="Central Park", tag="central", is_active=True)
    db_session.add(park)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_royal.id, park_id=park.id))
    db_session.commit()
    login_as(client, "royal", "secret")

    me = client.get("/auth/me")

    assert me.status_code == 200
    assert me.json()["parks"] == [
        {
            "id": park.id,
            "name": "Central Park",
            "tag": "central",
            "is_active": True,
            **PARK_DEFAULTS,
        }
    ]


@pytest.mark.parametrize("role", ["royal", "admin"])
def test_require_admin_allows_admin_roles(db_session: Session, role: str):
    user = User(
        username=role,
        password_hash="hash",
        role_id=role_id_for(db_session, role),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert require_admin(user, db_session) is user


def test_require_admin_rejects_non_admin(db_session: Session, seed_pending_operator):
    with pytest.raises(HTTPException) as exc_info:
        require_admin(seed_pending_operator, db_session)

    assert exc_info.value.status_code == 403


def test_require_approved_operator_allows_approved_operator(db_session: Session):
    user = User(
        username="operator",
        password_hash="hash",
        role_id=role_id_for(db_session, "operator"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert require_approved_operator(user) is user


@pytest.mark.parametrize(
    ("role", "access_status"),
    [("operator", "pending"), ("operator", "rejected"), ("admin", "approved")],
)
def test_require_approved_operator_rejects_other_users(
    db_session: Session, role: str, access_status: str
):
    user = User(
        username="user",
        password_hash="hash",
        role_id=role_id_for(db_session, role),
        access_status=access_status,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    with pytest.raises(HTTPException) as exc_info:
        require_approved_operator(user)

    assert exc_info.value.status_code == 403
