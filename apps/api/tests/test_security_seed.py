import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

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


@pytest.mark.parametrize(
    "stored_hash",
    ["", "not-a-hash", "$2b$12$abcdefghijklmnopqrstuv", "$argon2id$v=19$truncated"],
)
def test_verify_password_rejects_unparsable_hash(stored_hash):
    assert not verify_password("secret-pass", stored_hash)


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
    assert users[0].access_status == "approved"
    assert users[0].is_active
    assert verify_password("change-me", users[0].password_hash)


def test_ensure_seed_user_skips_incomplete_credentials(db_session, monkeypatch):
    monkeypatch.setenv("SEED_USERNAME", "royal")
    monkeypatch.delenv("SEED_PASSWORD", raising=False)

    ensure_seed_user(db_session, Settings())

    assert db_session.scalars(select(User)).all() == []


def test_ensure_seed_user_rejects_unknown_role(db_session):
    settings = Settings(
        _env_file=None,
        seed_username="royal",
        seed_password="change-me",
        seed_role="Royal",
    )

    with pytest.raises(ValueError, match="SEED_ROLE"):
        ensure_seed_user(db_session, settings)

    assert db_session.scalars(select(User)).all() == []


def test_settings_can_ignore_a_local_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SEED_USERNAME=leaked\nSEED_PASSWORD=leaked\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SEED_USERNAME", raising=False)
    monkeypatch.delenv("SEED_PASSWORD", raising=False)

    assert Settings(_env_file=None).seed_username is None
    assert Settings(_env_file=None).seed_password is None


def test_app_lifespan_ensures_seed_user(monkeypatch):
    from robopark_api import main

    calls = []
    settings = Settings(_env_file=None, seed_username=None, seed_password=None)
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


def test_app_lifespan_seeds_only_the_configured_database(
    db_engine, db_session, monkeypatch
):
    from robopark_api import main

    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_engine, future=True))
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            database_url=str(db_engine.url),
            seed_username="seeded",
            seed_password="seeded-pass",
            seed_role="royal",
        ),
    )

    with TestClient(main.create_app()):
        pass

    seeded = db_session.scalar(select(User).where(User.username == "seeded"))
    assert seeded is not None
    assert seeded.role == "royal"
    assert seeded.access_status == "approved"
