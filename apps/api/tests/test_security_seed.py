from sqlalchemy import select
from fastapi.testclient import TestClient

from robopark_api.config import Settings
from robopark_api.models import User
from robopark_api.security import (
    hash_password,
    hash_session_token,
    new_session_token,
    verify_password,
)
from robopark_api.seed import ensure_seed_user


def test_password_roundtrip():
    password_hash = hash_password("secret-pass")

    assert password_hash != "secret-pass"
    assert verify_password("secret-pass", password_hash)
    assert not verify_password("wrong", password_hash)


def test_session_token_hash_is_stable_sha256():
    assert hash_session_token("abc") == (
        "ba7816bf8f01cfea414140de5dae2223"
        "b00361a396177a9cb410ff61f20015ad"
    )


def test_new_session_token_is_random():
    first = new_session_token()
    second = new_session_token()

    assert first != second
    assert len(first) >= 32


def test_ensure_seed_user_creates_once(db_session, monkeypatch):
    monkeypatch.setenv("SEED_USERNAME", "royal")
    monkeypatch.setenv("SEED_PASSWORD", "change-me")
    monkeypatch.setenv("SEED_ROLE", "royal")
    settings = Settings()

    ensure_seed_user(db_session, settings)
    ensure_seed_user(db_session, settings)

    users = db_session.scalars(select(User)).all()
    assert len(users) == 1
    assert users[0].username == "royal"
    assert users[0].role == "royal"
    assert users[0].is_active
    assert verify_password("change-me", users[0].password_hash)


def test_ensure_seed_user_skips_incomplete_credentials(db_session, monkeypatch):
    monkeypatch.setenv("SEED_USERNAME", "royal")
    monkeypatch.delenv("SEED_PASSWORD", raising=False)

    ensure_seed_user(db_session, Settings())

    assert db_session.scalars(select(User)).all() == []


def test_app_lifespan_ensures_seed_user(monkeypatch):
    from robopark_api import main

    calls = []
    settings = Settings(seed_username=None, seed_password=None)
    db = object()

    class SessionContext:
        def __enter__(self):
            return db

        def __exit__(self, exc_type, exc_value, traceback):
            return None

    monkeypatch.setattr(main, "SessionLocal", SessionContext, raising=False)
    monkeypatch.setattr(
        main,
        "ensure_seed_user",
        lambda session, configured_settings: calls.append(
            (session, configured_settings)
        ),
        raising=False,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    with TestClient(main.create_app()):
        pass

    assert calls == [(db, settings)]
