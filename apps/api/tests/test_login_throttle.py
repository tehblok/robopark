"""Brute-force protection for authentication endpoints."""

import pytest

from conftest import VALID_PASSWORD, login_as
from robopark_api.security import PasswordPolicyError, validate_password
from robopark_api.services.login_throttle import LoginThrottle, reset_throttles


@pytest.fixture(autouse=True)
def clean_throttles():
    reset_throttles()
    yield
    reset_throttles()


# --- unit level ---------------------------------------------------------


def test_locks_after_max_attempts():
    throttle = LoginThrottle(max_attempts=3, window_seconds=60, lockout_seconds=300)

    for _ in range(2):
        throttle.register_failure("user", now=100.0)
        assert throttle.retry_after("user", now=100.0) == 0

    throttle.register_failure("user", now=100.0)
    assert throttle.retry_after("user", now=100.0) > 0


def test_lockout_expires():
    throttle = LoginThrottle(max_attempts=2, window_seconds=60, lockout_seconds=300)
    throttle.register_failure("user", now=100.0)
    throttle.register_failure("user", now=100.0)

    assert throttle.retry_after("user", now=200.0) > 0
    assert throttle.retry_after("user", now=500.0) == 0


def test_failures_outside_window_are_forgotten():
    throttle = LoginThrottle(max_attempts=3, window_seconds=60, lockout_seconds=300)
    throttle.register_failure("user", now=0.0)
    throttle.register_failure("user", now=10.0)
    # The first two failures fall out of the window before the third arrives.
    throttle.register_failure("user", now=200.0)

    assert throttle.retry_after("user", now=200.0) == 0


def test_success_resets_counter():
    throttle = LoginThrottle(max_attempts=2, window_seconds=60, lockout_seconds=300)
    throttle.register_failure("user", now=100.0)
    throttle.reset("user")
    throttle.register_failure("user", now=100.0)

    assert throttle.retry_after("user", now=100.0) == 0


def test_keys_are_isolated():
    throttle = LoginThrottle(max_attempts=1, window_seconds=60, lockout_seconds=300)
    throttle.register_failure("alice", now=100.0)

    assert throttle.retry_after("alice", now=100.0) > 0
    assert throttle.retry_after("bob", now=100.0) == 0


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


def test_successful_login_clears_counter(client, seed_royal, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "login_max_attempts", 3)

    assert login_as(client, "royal", "wrong").status_code == 401
    assert login_as(client, "royal", "secret").status_code == 204
    assert login_as(client, "royal", "wrong").status_code == 401
    assert login_as(client, "royal", "wrong").status_code == 401
    # Without the reset this fourth failure would already be a lockout.
    assert login_as(client, "royal", "wrong").status_code == 401


def test_register_shared_password_is_rate_limited(
    client, test_settings, monkeypatch
):
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
