from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.crypto import SecretDecryptionError, decrypt_secret, encrypt_secret
from robopark_api.models import (
    AccessStatus,
    PrivilegedAuthAudit,
    PrivilegedCredential,
    PrivilegedReauthorization,
    PrivilegedRecoveryCode,
    Role,
    User,
)
from robopark_api.security import verify_password

TOTP_STEP_SECONDS = 30
REAUTH_TTL_SECONDS = 120
RECOVERY_CODE_COUNT = 10


class PrivilegedAuthError(ValueError):
    pass


def enrolled(db: Session, user_id: int) -> bool:
    row = db.get(PrivilegedCredential, user_id)
    return bool(row and row.enrolled_at is not None)


def is_last_active_recovery_royal(db: Session, user: User) -> bool:
    """Whether removing this royal would leave no enrolled active royal."""
    if user.role != "royal" or not enrolled(db, user.id):
        return False
    replacement = db.scalar(
        select(User.id)
        .join(Role, User.role_id == Role.id)
        .join(PrivilegedCredential, PrivilegedCredential.user_id == User.id)
        .where(
            User.id != user.id,
            User.is_active.is_(True),
            User.access_status == AccessStatus.approved.value,
            Role.slug == "royal",
            PrivilegedCredential.enrolled_at.is_not(None),
        )
        .limit(1)
    )
    return replacement is None


def _unix_time() -> float:
    return time.time()


def _now() -> datetime:
    return datetime.fromtimestamp(_unix_time(), tz=UTC)


def _digest(value: str, key: str) -> str:
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _totp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def _key(settings: Settings) -> str:
    if not settings.secret_key:
        raise PrivilegedAuthError("secret_key_required")
    return settings.secret_key


def audit_decision(
    db: Session,
    *,
    actor: User,
    action: str,
    outcome: str,
    reason: str,
    ip: str | None,
    device: str | None,
    operation_kind: str | None = None,
    operation_id: str | None = None,
) -> None:
    db.add(
        PrivilegedAuthAudit(
            action=action,
            outcome=outcome,
            actor_user_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role,
            reason=reason,
            client_ip=ip,
            device=(device or "")[:256] or None,
            operation_kind=operation_kind,
            operation_id=operation_id,
        )
    )
    db.commit()


def begin_enrollment(db: Session, actor: User, settings: Settings) -> str:
    row = db.get(PrivilegedCredential, actor.id)
    if row is not None and row.enrolled_at is not None:
        raise PrivilegedAuthError("already_enrolled")
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    encrypted = encrypt_secret(secret, _key(settings))
    if row is None:
        db.add(PrivilegedCredential(user_id=actor.id, totp_secret_encrypted=encrypted))
    else:
        row.totp_secret_encrypted = encrypted
        row.last_totp_counter = None
    db.commit()
    return secret


def _matching_counter(secret: str, code: str, current: int) -> int | None:
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    for counter in range(current - 1, current + 2):
        if hmac.compare_digest(_totp(secret, counter), code):
            return counter
    return None


def _lock_credential(db: Session, user_id: int) -> PrivilegedCredential | None:
    # A no-op write serializes SQLite workers; FOR UPDATE is ineffective there.
    db.execute(
        update(PrivilegedCredential)
        .where(PrivilegedCredential.user_id == user_id)
        .values(user_id=PrivilegedCredential.user_id)
    )
    return db.scalar(
        select(PrivilegedCredential)
        .where(PrivilegedCredential.user_id == user_id)
        .with_for_update()
    )


def confirm_enrollment(
    db: Session, actor: User, settings: Settings, *, password: str, code: str
) -> list[str]:
    row = _lock_credential(db, actor.id)
    if row is None or row.enrolled_at is not None:
        raise PrivilegedAuthError("enrollment_not_pending")
    if not verify_password(password, actor.password_hash):
        raise PrivilegedAuthError("invalid_credentials")
    try:
        secret = decrypt_secret(row.totp_secret_encrypted, _key(settings))
    except SecretDecryptionError as exc:
        raise PrivilegedAuthError("credential_unavailable") from exc
    counter = int(_unix_time() // TOTP_STEP_SECONDS)
    matched = _matching_counter(secret or "", code, counter)
    if matched is None:
        raise PrivilegedAuthError("invalid_credentials")
    codes = [secrets.token_urlsafe(18) for _ in range(RECOVERY_CODE_COUNT)]
    db.add_all(
        PrivilegedRecoveryCode(
            user_id=actor.id, code_hash=_digest(value, _key(settings))
        )
        for value in codes
    )
    row.last_totp_counter = matched
    row.enrolled_at = _now()
    db.commit()
    return codes


def _verify_second_factor(
    db: Session, actor: User, settings: Settings, code: str
) -> bool:
    row = _lock_credential(db, actor.id)
    if row is None or row.enrolled_at is None:
        raise PrivilegedAuthError("privileged_enrollment_required")
    secret = decrypt_secret(row.totp_secret_encrypted, _key(settings)) or ""
    current = int(_unix_time() // TOTP_STEP_SECONDS)
    matched = _matching_counter(secret, code, current)
    if matched is not None:
        if row.last_totp_counter is not None and matched <= row.last_totp_counter:
            return False
        row.last_totp_counter = matched
        return True
    code_hash = _digest(code, _key(settings))
    recovery = db.scalar(
        select(PrivilegedRecoveryCode).where(
            PrivilegedRecoveryCode.user_id == actor.id,
            PrivilegedRecoveryCode.code_hash == code_hash,
            PrivilegedRecoveryCode.used_at.is_(None),
        )
    )
    if recovery is None or not hmac.compare_digest(recovery.code_hash, code_hash):
        return False
    recovery.used_at = _now()
    return True


def issue_reauthorization(
    db: Session,
    actor: User,
    settings: Settings,
    *,
    session_token_hash: str,
    password: str,
    code: str,
    operation_kind: str,
    operation_id: str,
) -> str:
    if not verify_password(password, actor.password_hash) or not _verify_second_factor(
        db, actor, settings, code
    ):
        db.rollback()
        raise PrivilegedAuthError("invalid_credentials")
    raw = secrets.token_urlsafe(32)
    db.add(
        PrivilegedReauthorization(
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            user_id=actor.id,
            session_token_hash=session_token_hash,
            operation_kind=operation_kind,
            operation_id=operation_id,
            expires_at=_now() + timedelta(seconds=REAUTH_TTL_SECONDS),
        )
    )
    db.commit()
    return raw


def consume_reauthorization(
    db: Session,
    actor: User,
    *,
    raw_token: str,
    session_token_hash: str,
    operation_kind: str,
    operation_id: str,
) -> bool:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    db.execute(
        update(PrivilegedReauthorization)
        .where(PrivilegedReauthorization.token_hash == token_hash)
        .values(token_hash=PrivilegedReauthorization.token_hash)
    )
    row = db.scalar(
        select(PrivilegedReauthorization).where(
            PrivilegedReauthorization.token_hash == token_hash,
            PrivilegedReauthorization.user_id == actor.id,
            PrivilegedReauthorization.used_at.is_(None),
        ).with_for_update()
    )
    now = _now()
    if row is None:
        return False
    expires = row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    valid = (
        expires > now
        and hmac.compare_digest(row.session_token_hash, session_token_hash)
        and hmac.compare_digest(row.operation_kind, operation_kind)
        and hmac.compare_digest(row.operation_id, operation_id)
    )
    if not valid:
        return False
    row.used_at = now
    db.commit()
    return True
