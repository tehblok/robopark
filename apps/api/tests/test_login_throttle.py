"""Brute-force protection for authentication endpoints."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from conftest import VALID_PASSWORD, login_as
from robopark_api.models import AuthThrottleState
from robopark_api.security import PasswordPolicyError, validate_password
from robopark_api.services.login_throttle import LoginThrottle, reset_throttles


@pytest.fixture(autouse=True)
def clean_throttles():
    reset_throttles()
    yield
    reset_throttles()


# --- unit level ---------------------------------------------------------


def _throttle(db_engine, *, max_attempts=3):
    return LoginThrottle(
        session_factory=sessionmaker(bind=db_engine, future=True),
        max_attempts=max_attempts,
        window_seconds=60,
        lockout_seconds=300,
    )


def test_locks_after_max_attempts(db_engine):
    throttle = _throttle(db_engine)
    now = datetime(2026, 9, 20, tzinfo=UTC)

    for _ in range(2):
        throttle.register_failure("user|127.0.0.1", now=now)
        assert throttle.retry_after("user|127.0.0.1", now=now) == 0

    throttle.register_failure("user|127.0.0.1", now=now)
    assert throttle.retry_after("user|127.0.0.1", now=now) > 0


def test_lockout_expires(db_engine):
    throttle = _throttle(db_engine, max_attempts=2)
    now = datetime(2026, 9, 20, tzinfo=UTC)
    throttle.register_failure("user|127.0.0.1", now=now)
    throttle.register_failure("user|127.0.0.1", now=now)

    assert throttle.retry_after("user|127.0.0.1", now=now + timedelta(seconds=100)) > 0
    assert throttle.retry_after("user|127.0.0.1", now=now + timedelta(seconds=400)) == 0


def test_failures_outside_window_are_forgotten(db_engine):
    throttle = _throttle(db_engine)
    now = datetime(2026, 9, 20, tzinfo=UTC)
    throttle.register_failure("user|127.0.0.1", now=now)
    throttle.register_failure("user|127.0.0.1", now=now + timedelta(seconds=10))
    # The first two failures fall out of the window before the third arrives.
    throttle.register_failure("user|127.0.0.1", now=now + timedelta(seconds=200))

    assert throttle.retry_after("user|127.0.0.1", now=now + timedelta(seconds=200)) == 0


def test_success_resets_counter(db_engine):
    throttle = _throttle(db_engine, max_attempts=2)
    now = datetime(2026, 9, 20, tzinfo=UTC)
    throttle.register_failure("user|127.0.0.1", now=now)
    throttle.reset("user|127.0.0.1")
    throttle.register_failure("user|127.0.0.1", now=now)

    assert throttle.retry_after("user|127.0.0.1", now=now) == 0


def test_keys_are_isolated(db_engine):
    throttle = _throttle(db_engine, max_attempts=1)
    now = datetime(2026, 9, 20, tzinfo=UTC)
    throttle.register_failure("alice|127.0.0.1", now=now)

    assert throttle.retry_after("alice|127.0.0.1", now=now) > 0
    assert throttle.retry_after("bob|127.0.0.1", now=now) == 0


def test_failures_are_shared_between_service_instances(db_engine):
    first = _throttle(db_engine, max_attempts=2)
    second = _throttle(db_engine, max_attempts=2)
    now = datetime(2026, 9, 20, tzinfo=UTC)

    first.register_failure("alice|192.0.2.1", now=now)
    second.register_failure("alice|192.0.2.1", now=now)

    assert first.retry_after("alice|192.0.2.1", now=now) == 300


def test_database_stores_only_sha256_key(db_engine):
    throttle = _throttle(db_engine)
    raw_key = "Alice|192.0.2.7"

    throttle.register_failure(raw_key, now=datetime(2026, 9, 20, tzinfo=UTC))

    with db_engine.connect() as connection:
        stored = connection.execute(select(AuthThrottleState.key_hash)).scalar_one()
    assert stored == "7a405606489c11a516f7996332ac71cdb21dd6f8ed1dc14480013d1e51c1ef82"
    assert "Alice" not in stored


# --- endpoint level -----------------------------------------------------


def test_login_is_rate_limited(client, seed_royal, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "login_max_attempts", 3)

    for _ in range(3):
        assert login_as(client, "royal", "wrong").status_code == 401

    blocked = login_as(client, "royal", "wrong")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers

    # Even the correct password is refused while the lockout holds.
    assert login_as(client, "royal", "secret").status_code == 429


def test_login_rotating_usernames_reaches_shared_ip_limit(client, test_settings, monkeypatch):
    monkeypatch.setitem(test_settings.__dict__, "login_ip_max_attempts", 3)
    for index in range(3):
        assert login_as(client, f"unknown-{index}", "wrong").status_code == 401
    response = login_as(client, "another-unknown", "wrong")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_success_does_not_reset_failures_from_other_usernames(
    client, seed_royal, test_settings, monkeypatch
):
    monkeypatch.setitem(test_settings.__dict__, "login_ip_max_attempts", 3)
    assert login_as(client, "unknown-one", "wrong").status_code == 401
    assert login_as(client, "royal", "secret").status_code == 204
    assert login_as(client, "unknown-two", "wrong").status_code == 401
    assert login_as(client, "unknown-three", "wrong").status_code == 401
    assert login_as(client, "unknown-four", "wrong").status_code == 429


def test_unknown_and_inactive_users_still_verify_a_password_hash(
    client, seed_royal, db_session, monkeypatch
):
    from robopark_api.routers import auth

    hashes = []
    monkeypatch.setattr(
        auth, "verify_password", lambda _password, encoded: hashes.append(encoded) or False
    )
    assert login_as(client, "royal", "wrong").status_code == 401
    assert login_as(client, "unknown-user", "wrong").status_code == 401
    seed_royal.is_active = False
    db_session.commit()
    assert login_as(client, "royal", "wrong").status_code == 401
    assert len(hashes) == 3
    assert all(value.startswith("$argon2id$") for value in hashes)
    assert hashes[1] == hashes[2]


def test_login_rejects_excess_concurrent_password_work_and_recovers(client, seed_royal):
    from robopark_api.routers import auth

    slots = auth._password_verification_slots
    for _ in range(4):
        assert slots.acquire(blocking=False)
    try:
        response = login_as(client, "royal", "secret")
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "1"
    finally:
        for _ in range(4):
            slots.release()
    assert login_as(client, "royal", "secret").status_code == 204


def test_password_verification_exception_releases_concurrency_slot(client, seed_royal, monkeypatch):
    from robopark_api.routers import auth

    def fail(*_args):
        raise RuntimeError("verification failed")

    monkeypatch.setattr(auth, "verify_password", fail)
    with pytest.raises(RuntimeError, match="verification failed"):
        login_as(client, "royal", "secret")
    slots = auth._password_verification_slots
    acquired = 0
    try:
        for _ in range(4):
            assert slots.acquire(blocking=False)
            acquired += 1
    finally:
        for _ in range(acquired):
            slots.release()


def test_successful_login_clears_counter(client, seed_royal, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "login_max_attempts", 3)

    assert login_as(client, "royal", "wrong").status_code == 401
    assert login_as(client, "royal", "secret").status_code == 204
    assert login_as(client, "royal", "wrong").status_code == 401
    assert login_as(client, "royal", "wrong").status_code == 401
    # Without the reset this fourth failure would already be a lockout.
    assert login_as(client, "royal", "wrong").status_code == 401


def test_register_shared_password_is_rate_limited(client, test_settings, monkeypatch):
    """The shared registration password must not be brute-forceable."""
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    monkeypatch.setattr(test_settings, "register_max_attempts", 3)

    for _ in range(3):
        response = client.post(
            "/auth/register",
            json={
                "shared_password": "wrong",
                "username": "attacker",
                "password": VALID_PASSWORD,
            },
        )
        assert response.status_code == 403

    blocked = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "attacker",
            "password": VALID_PASSWORD,
        },
    )
    assert blocked.status_code == 429


def test_register_cannot_bypass_shared_password_limit_by_rotating_username(
    client, test_settings, monkeypatch
):
    monkeypatch.setattr(test_settings, "operator_shared_password", "gate")
    monkeypatch.setattr(test_settings, "register_max_attempts", 2)

    for username in ("attacker-one", "attacker-two"):
        response = client.post(
            "/auth/register",
            json={
                "shared_password": "wrong",
                "username": username,
                "password": VALID_PASSWORD,
            },
        )
        assert response.status_code == 403

    blocked = client.post(
        "/auth/register",
        json={
            "shared_password": "gate",
            "username": "attacker-three",
            "password": VALID_PASSWORD,
        },
    )
    assert blocked.status_code == 429


# --- password policy ----------------------------------------------------


@pytest.mark.parametrize(
    "password",
    ["a", "short", "password1234", "alllowercaseletters", "1234567890123"],
)
def test_weak_passwords_rejected(password):
    with pytest.raises(PasswordPolicyError):
        validate_password(password, min_length=12, require_complexity=True)


@pytest.mark.parametrize(
    "password",
    ["Str0ng-Pass!2026", "correct horse Battery 9", "Xk7#mQp2vLzR"],
)
def test_strong_passwords_accepted(password):
    validate_password(password, min_length=12, require_complexity=True)


def test_password_cannot_contain_username():
    with pytest.raises(PasswordPolicyError):
        validate_password("Mechanic-2026!", min_length=12, username="mechanic")


def test_policy_length_is_configurable():
    validate_password("Ab3!xyz", min_length=6, require_complexity=True)
