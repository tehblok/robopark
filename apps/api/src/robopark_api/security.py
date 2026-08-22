import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_password_hasher = PasswordHasher()


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
