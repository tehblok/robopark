"""Encryption at rest for integration secrets (Tracker token, Emergency cookie).

Secrets live in ``platform_settings`` and used to be stored as plaintext, so a
copy of ``robopark.db`` handed over a working Tracker OAuth token. Values are
now encrypted with Fernet (AES-128-CBC + HMAC) using a key derived from
``SECRET_KEY``.

Design notes:

* Ciphertext is tagged with :data:`ENC_PREFIX`. Anything without the prefix is
  treated as a legacy plaintext value and returned as-is, so an existing
  database keeps working; values are re-encrypted on the next write / migrate.
* Writing secrets without ``SECRET_KEY`` raises :class:`MissingSecretKeyError`
  (fail closed). Set ``SECRET_KEY`` in `.env` / ``host.env`` before storing
  Tracker or Emergency credentials.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENC_PREFIX = "enc:v1:"


class SecretDecryptionError(Exception):
    """Stored ciphertext cannot be decrypted with the configured key."""


class MissingSecretKeyError(ValueError):
    """SECRET_KEY is required to encrypt secrets at rest."""


def _derive_fernet_key(secret_key: str) -> bytes:
    """Derive a urlsafe-base64 Fernet key from an arbitrary passphrase."""
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _cipher(secret_key: str) -> Fernet:
    return Fernet(_derive_fernet_key(secret_key))


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(ENC_PREFIX)


def encrypt_secret(value: str, secret_key: str | None) -> str:
    """Encrypt *value*. Raises if no ``SECRET_KEY`` is configured."""
    if not value:
        return value
    if not secret_key:
        raise MissingSecretKeyError(
            "SECRET_KEY is required to store integration secrets at rest"
        )
    token = _cipher(secret_key).encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENC_PREFIX}{token}"


def decrypt_secret(value: str | None, secret_key: str | None) -> str | None:
    """Decrypt *value* written by :func:`encrypt_secret`.

    Legacy plaintext values (no prefix) pass through untouched.
    """
    if not value:
        return value
    if not is_encrypted(value):
        return value
    if not secret_key:
        raise SecretDecryptionError("stored secret is encrypted but SECRET_KEY is not configured")
    token = value[len(ENC_PREFIX) :].encode("ascii")
    try:
        return _cipher(secret_key).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise SecretDecryptionError(
            "stored secret cannot be decrypted — SECRET_KEY likely changed"
        ) from exc


def generate_secret_key() -> str:
    """Generate a fresh key suitable for the SECRET_KEY setting."""
    return Fernet.generate_key().decode("ascii")
