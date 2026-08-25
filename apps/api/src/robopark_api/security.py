import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_password_hasher = PasswordHasher()

#: Passwords that are long enough to pass the length rule but still trivial.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "passw0rd",
        "password1",
        "password123",
        "qwerty123",
        "qwertyuiop",
        "123456789",
        "1234567890",
        "letmein123",
        "adminadmin",
        "welcome123",
        "iloveyou123",
        "robopark",
        "robopark123",
    }
)


class PasswordPolicyError(ValueError):
    """Raised when a password does not satisfy the configured policy."""


def validate_password(
    password: str,
    *,
    min_length: int = 12,
    require_complexity: bool = True,
    username: str | None = None,
) -> None:
    """Validate a new password, raising :class:`PasswordPolicyError` on failure.

    The API previously accepted any non-empty string, so a one-character
    password was valid for operators and admin-created mechanics alike.
    """
    if len(password) < min_length:
        raise PasswordPolicyError(
            f"password must be at least {min_length} characters long"
        )

    normalized = password.strip().lower()
    if normalized in _COMMON_PASSWORDS:
        raise PasswordPolicyError("password is too common")

    if username and username.strip().lower() in normalized:
        raise PasswordPolicyError("password must not contain the username")

    if require_complexity:
        classes = (
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        )
        if sum(classes) < 3:
            raise PasswordPolicyError(
                "password must combine at least three of: lowercase, uppercase, "
                "digits, special characters"
            )


def hash_password(plain: str) -> str:
    return _password_hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    # InvalidHashError is a ValueError, not a VerificationError, so a stored
    # hash the app cannot parse needs its own arm to stay a 401 and not a 500.
    try:
        return _password_hasher.verify(password_hash, plain)
    except (VerificationError, InvalidHashError):
        return False


def shared_passwords_match(provided: str, expected: str | None) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)
