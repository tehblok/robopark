from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.crypto import SecretDecryptionError, decrypt_secret, encrypt_secret
from robopark_api.models import (
    AccessStatus,
    PrivilegedAuthAudit,
    PrivilegedCredential,
    PrivilegedReauthorization,
    PrivilegedRecoveryCode,
    PrivilegedRecoveryReset,
    PrivilegedRecoveryResetCode,
    Role,
    User,
)
from robopark_api.security import verify_password
from robopark_api.services.login_throttle import LoginThrottle

TOTP_STEP_SECONDS = 30
REAUTH_TTL_SECONDS = 120
RECOVERY_RESET_TTL_SECONDS = 600
RECOVERY_CODE_COUNT = 10
RECOVERY_HASH_VERSION = "scrypt-v1"
LEGACY_RECOVERY_HASH_VERSION = "legacy-hmac-v1"


@dataclass(frozen=True)
class AuditContext:
    ip: str | None
    device: str | None
    session_token_hash: str | None = None
    operation_kind: str | None = None
    operation_id: str | None = None
    capability_revision: str | None = None


class PrivilegedAuthError(ValueError):
    def __init__(self, reason: str, *, status_code: int = 401, retry_after: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code
        self.retry_after = retry_after


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


def _recovery_hash(code: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(12)
    digest = hashlib.scrypt(code.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    encoded_salt = base64.urlsafe_b64encode(salt).decode().rstrip("=")
    encoded_digest = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"v2${encoded_salt}${encoded_digest}"


def _recovery_matches(code: str, encoded: str) -> bool:
    try:
        marker, raw_salt, _digest = encoded.split("$", 2)
        if marker != "v2":
            return False
        salt = base64.urlsafe_b64decode(raw_salt + "=" * (-len(raw_salt) % 4))
    except (ValueError, UnicodeError):
        return False
    return hmac.compare_digest(_recovery_hash(code, salt=salt).encode(), encoded.encode())


def _new_recovery_rows(user_id: int) -> tuple[list[str], list[PrivilegedRecoveryCode]]:
    codes = [secrets.token_urlsafe(18) for _ in range(RECOVERY_CODE_COUNT)]
    rows = [
        PrivilegedRecoveryCode(
            user_id=user_id,
            code_hash=_recovery_hash(code),
            hash_version=RECOVERY_HASH_VERSION,
        )
        for code in codes
    ]
    return codes, rows


def _new_pending_recovery_rows(
    reset_id: str,
) -> tuple[list[str], list[PrivilegedRecoveryResetCode]]:
    codes = [secrets.token_urlsafe(18) for _ in range(RECOVERY_CODE_COUNT)]
    rows = [
        PrivilegedRecoveryResetCode(
            reset_id=reset_id,
            code_hash=_recovery_hash(code),
            hash_version=RECOVERY_HASH_VERSION,
        )
        for code in codes
    ]
    return codes, rows


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


def _add_audit(
    db: Session,
    *,
    actor: User,
    action: str,
    outcome: str,
    reason: str,
    context: AuditContext,
) -> None:
    db.add(
        PrivilegedAuthAudit(
            action=action,
            outcome=outcome,
            actor_user_id=actor.id,
            actor_username=actor.username,
            actor_role=actor.role,
            reason=reason,
            client_ip=context.ip,
            device=(context.device or "")[:256] or None,
            operation_kind=context.operation_kind,
            operation_id=context.operation_id,
            capability_revision=context.capability_revision,
        )
    )


def _commit(db: Session) -> None:
    try:
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _atomic(function):
    @wraps(function)
    def guarded(db: Session, *args: Any, **kwargs: Any):
        try:
            return function(db, *args, **kwargs)
        except BaseException:
            db.rollback()
            raise

    return guarded


def _deny(
    db: Session,
    actor: User,
    *,
    action: str,
    reason: str,
    context: AuditContext,
    status_code: int = 401,
    throttle: LoginThrottle | None = None,
    throttle_key: str | None = None,
    retry_after: int | None = None,
) -> None:
    try:
        if throttle is not None and throttle_key is not None and retry_after is None:
            throttle.register_failure(throttle_key, db=db, commit=False)
        _add_audit(
            db,
            actor=actor,
            action=action,
            outcome="denied",
            reason=reason,
            context=context,
        )
        _commit(db)
    except BaseException:
        db.rollback()
        raise
    raise PrivilegedAuthError(reason, status_code=status_code, retry_after=retry_after)


@_atomic
def begin_enrollment(
    db: Session, actor: User, settings: Settings, *, context: AuditContext
) -> str:
    row = db.get(PrivilegedCredential, actor.id)
    if row is not None and row.enrolled_at is not None:
        _deny(
            db,
            actor,
            action="privileged.enrollment.begin",
            reason="already_enrolled",
            context=context,
            status_code=409,
        )
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    try:
        encryption_key = _key(settings)
    except PrivilegedAuthError:
        _deny(
            db,
            actor,
            action="privileged.enrollment.begin",
            reason="secret_key_required",
            context=context,
            status_code=409,
        )
    encrypted = encrypt_secret(secret, encryption_key)
    if row is None:
        db.add(PrivilegedCredential(user_id=actor.id, totp_secret_encrypted=encrypted))
    else:
        row.totp_secret_encrypted = encrypted
        row.last_totp_counter = None
    _add_audit(
        db,
        actor=actor,
        action="privileged.enrollment.begin",
        outcome="success",
        reason="pending",
        context=context,
    )
    _commit(db)
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


def _advance_generation_and_revoke(
    db: Session, credential: PrivilegedCredential, *, now: datetime
) -> None:
    credential.credential_generation += 1
    db.execute(
        update(PrivilegedReauthorization)
        .where(
            PrivilegedReauthorization.user_id == credential.user_id,
            PrivilegedReauthorization.used_at.is_(None),
        )
        .values(used_at=now)
    )


@_atomic
def confirm_enrollment(
    db: Session,
    actor: User,
    settings: Settings,
    *,
    password: str,
    code: str,
    context: AuditContext,
    throttle: LoginThrottle,
    throttle_key: str,
) -> list[str]:
    retry = throttle.retry_after(throttle_key, db=db)
    if retry:
        _deny(
            db,
            actor,
            action="privileged.enrollment.confirm",
            reason="locked",
            context=context,
            status_code=429,
            retry_after=retry,
        )
    row = _lock_credential(db, actor.id)
    if row is None or row.enrolled_at is not None:
        _deny(
            db,
            actor,
            action="privileged.enrollment.confirm",
            reason="enrollment_not_pending",
            context=context,
            throttle=throttle,
            throttle_key=throttle_key,
        )
    if not verify_password(password, actor.password_hash):
        _deny(
            db,
            actor,
            action="privileged.enrollment.confirm",
            reason="invalid_credentials",
            context=context,
            throttle=throttle,
            throttle_key=throttle_key,
        )
    try:
        secret = decrypt_secret(row.totp_secret_encrypted, _key(settings))
    except (SecretDecryptionError, PrivilegedAuthError):
        _deny(
            db,
            actor,
            action="privileged.enrollment.confirm",
            reason="credential_unavailable",
            context=context,
            status_code=409,
        )
    counter = int(_unix_time() // TOTP_STEP_SECONDS)
    matched = _matching_counter(secret or "", code, counter)
    if matched is None:
        _deny(
            db,
            actor,
            action="privileged.enrollment.confirm",
            reason="invalid_credentials",
            context=context,
            throttle=throttle,
            throttle_key=throttle_key,
        )
    codes, recovery_rows = _new_recovery_rows(actor.id)
    db.add_all(recovery_rows)
    row.last_totp_counter = matched
    row.enrolled_at = _now()
    _advance_generation_and_revoke(db, row, now=_now())
    throttle.reset(throttle_key, db=db, commit=False)
    _add_audit(
        db,
        actor=actor,
        action="privileged.enrollment.confirm",
        outcome="success",
        reason="enrolled",
        context=context,
    )
    _commit(db)
    return codes


def _verify_second_factor(
    db: Session, actor: User, settings: Settings, code: str
) -> tuple[bool, str]:
    row = _lock_credential(db, actor.id)
    if row is None or row.enrolled_at is None:
        return False, "privileged_enrollment_required"
    if not (len(code) == 6 and code.isascii() and code.isdigit()):
        recovery, row = _match_recovery(db, actor, code)
        if recovery == "matched" and row is not None:
            row.used_at = _now()
            return True, "recovery"
        if recovery == "unsupported":
            return False, "recovery_hash_unsupported"
        return False, "invalid_credentials"
    try:
        secret = decrypt_secret(row.totp_secret_encrypted, _key(settings)) or ""
    except (SecretDecryptionError, PrivilegedAuthError):
        return False, "credential_unavailable"
    current = int(_unix_time() // TOTP_STEP_SECONDS)
    matched = _matching_counter(secret, code, current)
    if matched is not None:
        if row.last_totp_counter is not None and matched <= row.last_totp_counter:
            return False, "invalid_credentials"
        row.last_totp_counter = matched
        return True, "totp"
    return False, "invalid_credentials"


def _match_recovery(
    db: Session, actor: User, code: str
) -> tuple[str, PrivilegedRecoveryCode | None]:
    rows = list(
        db.scalars(
            select(PrivilegedRecoveryCode)
            .where(
                PrivilegedRecoveryCode.user_id == actor.id,
                PrivilegedRecoveryCode.used_at.is_(None),
            )
            .with_for_update()
        )
    )
    supported = [row for row in rows if row.hash_version == RECOVERY_HASH_VERSION]
    for row in supported:
        if _recovery_matches(code, row.code_hash):
            return "matched", row
    if not supported and any(row.hash_version == LEGACY_RECOVERY_HASH_VERSION for row in rows):
        return "unsupported", None
    return "invalid", None


@_atomic
def issue_reauthorization(
    db: Session,
    actor: User,
    settings: Settings,
    *,
    password: str,
    code: str,
    context: AuditContext,
    throttle: LoginThrottle,
    throttle_key: str,
    validate_context: Callable[[], tuple[str, int] | None] | None = None,
) -> str:
    retry = throttle.retry_after(throttle_key, db=db)
    if retry:
        _deny(
            db,
            actor,
            action="privileged.reauthorize",
            reason="locked",
            context=context,
            status_code=429,
            retry_after=retry,
        )
    valid, reason = False, "invalid_credentials"
    if verify_password(password, actor.password_hash):
        valid, reason = _verify_second_factor(db, actor, settings, code)
    if not valid:
        status_code = 409 if reason in {
            "credential_unavailable",
            "privileged_enrollment_required",
            "recovery_hash_unsupported",
        } else 401
        _deny(
            db,
            actor,
            action="privileged.reauthorize",
            reason=reason,
            context=context,
            status_code=status_code,
            throttle=throttle,
            throttle_key=throttle_key,
        )
    credential = db.get(PrivilegedCredential, actor.id)
    if credential is None:
        _deny(
            db,
            actor,
            action="privileged.reauthorize",
            reason="privileged_enrollment_required",
            context=context,
            status_code=409,
        )
    invalid_context = validate_context() if validate_context is not None else None
    if invalid_context is not None:
        reason, status_code = invalid_context
        _deny(
            db,
            actor,
            action="privileged.reauthorize",
            reason=reason,
            context=context,
            status_code=status_code,
        )
    raw = secrets.token_urlsafe(32)
    db.add(
        PrivilegedReauthorization(
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            user_id=actor.id,
            session_token_hash=context.session_token_hash or "",
            operation_kind=context.operation_kind or "",
            operation_id=context.operation_id or "",
            capability_revision=context.capability_revision,
            credential_generation=credential.credential_generation,
            expires_at=_now() + timedelta(seconds=REAUTH_TTL_SECONDS),
        )
    )
    throttle.reset(throttle_key, db=db, commit=False)
    _add_audit(
        db,
        actor=actor,
        action="privileged.reauthorize",
        outcome="success",
        reason="issued",
        context=context,
    )
    _commit(db)
    return raw


@_atomic
def consume_reauthorization(
    db: Session,
    actor: User,
    *,
    raw_token: str,
    context: AuditContext,
) -> bool:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    credential = _lock_credential(db, actor.id)
    db.execute(
        update(PrivilegedReauthorization)
        .where(PrivilegedReauthorization.token_hash == token_hash)
        .values(token_hash=PrivilegedReauthorization.token_hash)
    )
    row = db.scalar(
        select(PrivilegedReauthorization).where(
            PrivilegedReauthorization.token_hash == token_hash,
            PrivilegedReauthorization.user_id == actor.id,
        ).with_for_update()
    )
    now = _now()
    expires = None
    if row is not None:
        expires = row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    valid = bool(
        row is not None
        and expires is not None
        and row.used_at is None
        and expires > now
        and credential is not None
        and row.credential_generation == credential.credential_generation
        and hmac.compare_digest(row.session_token_hash, context.session_token_hash or "")
        and hmac.compare_digest(row.operation_kind, context.operation_kind or "")
        and hmac.compare_digest(row.operation_id, context.operation_id or "")
        and hmac.compare_digest(row.capability_revision or "", context.capability_revision or "")
    )
    if valid:
        row.used_at = now
    generation_mismatch = bool(
        row is not None
        and credential is not None
        and row.credential_generation != credential.credential_generation
    )
    revision_mismatch = bool(
        row is not None
        and not hmac.compare_digest(
            row.capability_revision or "", context.capability_revision or ""
        )
    )
    _add_audit(
        db,
        actor=actor,
        action="privileged.operation",
        outcome="success" if valid else "denied",
        reason="authorized"
        if valid
        else (
            "credential_generation_mismatch"
            if generation_mismatch
            else "capability_revision_mismatch"
            if revision_mismatch
            else ("token_invalid" if credential and credential.enrolled_at else "not_enrolled")
        ),
        context=context,
    )
    _commit(db)
    return valid


def _delete_pending_reset(db: Session, pending_id: str) -> None:
    db.execute(
        delete(PrivilegedRecoveryResetCode).where(
            PrivilegedRecoveryResetCode.reset_id == pending_id
        )
    )
    db.execute(
        delete(PrivilegedRecoveryReset).where(PrivilegedRecoveryReset.id == pending_id)
    )


@_atomic
def begin_recovery_reset(
    db: Session,
    actor: User,
    settings: Settings,
    *,
    password: str,
    code: str,
    context: AuditContext,
    throttle: LoginThrottle,
    throttle_key: str,
) -> tuple[str, str, list[str]]:
    retry = throttle.retry_after(throttle_key, db=db)
    if retry:
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.begin",
            reason="locked",
            context=context,
            status_code=429,
            retry_after=retry,
        )
    try:
        encryption_key = _key(settings)
    except PrivilegedAuthError:
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.begin",
            reason="secret_key_required",
            context=context,
            status_code=409,
        )
    row = _lock_credential(db, actor.id)
    result, selected = (
        _match_recovery(db, actor, code)
        if verify_password(password, actor.password_hash)
        else ("invalid", None)
    )
    if row is None or row.enrolled_at is None or result != "matched" or selected is None:
        reason = "recovery_hash_unsupported" if result == "unsupported" else "invalid_credentials"
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.begin",
            reason=reason,
            context=context,
            status_code=409 if reason == "recovery_hash_unsupported" else 401,
            throttle=throttle,
            throttle_key=throttle_key,
        )
    previous = db.scalar(
        select(PrivilegedRecoveryReset)
        .where(PrivilegedRecoveryReset.user_id == actor.id)
        .with_for_update()
    )
    if previous is not None:
        _delete_pending_reset(db, previous.id)
    pending_id = secrets.token_urlsafe(24)
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    db.add(
        PrivilegedRecoveryReset(
            id=pending_id,
            user_id=actor.id,
            selected_recovery_code_id=selected.id,
            totp_secret_encrypted=encrypt_secret(secret, encryption_key),
            expires_at=_now() + timedelta(seconds=RECOVERY_RESET_TTL_SECONDS),
        )
    )
    codes, pending_rows = _new_pending_recovery_rows(pending_id)
    db.add_all(pending_rows)
    throttle.reset(throttle_key, db=db, commit=False)
    _add_audit(
        db,
        actor=actor,
        action="privileged.recovery.reset.begin",
        outcome="success",
        reason="pending",
        context=context,
    )
    _commit(db)
    return pending_id, secret, codes


@_atomic
def confirm_recovery_reset(
    db: Session,
    actor: User,
    settings: Settings,
    *,
    pending_id: str,
    code: str,
    context: AuditContext,
    throttle: LoginThrottle,
    throttle_key: str,
) -> None:
    retry = throttle.retry_after(throttle_key, db=db)
    if retry:
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.confirm",
            reason="locked",
            context=context,
            status_code=429,
            retry_after=retry,
        )
    credential = _lock_credential(db, actor.id)
    pending = db.scalar(
        select(PrivilegedRecoveryReset)
        .where(
            PrivilegedRecoveryReset.id == pending_id,
            PrivilegedRecoveryReset.user_id == actor.id,
        )
        .with_for_update()
    )
    if credential is None or credential.enrolled_at is None or pending is None:
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.confirm",
            reason="pending_reset_invalid",
            context=context,
            status_code=409,
            throttle=throttle,
            throttle_key=throttle_key,
        )
    expires = (
        pending.expires_at.replace(tzinfo=UTC)
        if pending.expires_at.tzinfo is None
        else pending.expires_at
    )
    if expires <= _now():
        _delete_pending_reset(db, pending.id)
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.confirm",
            reason="pending_reset_expired",
            context=context,
            status_code=410,
        )
    try:
        secret = decrypt_secret(pending.totp_secret_encrypted, _key(settings)) or ""
    except (SecretDecryptionError, PrivilegedAuthError):
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.confirm",
            reason="credential_unavailable",
            context=context,
            status_code=409,
        )
    matched = _matching_counter(secret, code, int(_unix_time() // TOTP_STEP_SECONDS))
    if matched is None:
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.confirm",
            reason="invalid_credentials",
            context=context,
            throttle=throttle,
            throttle_key=throttle_key,
        )
    selected = db.scalar(
        select(PrivilegedRecoveryCode)
        .where(
            PrivilegedRecoveryCode.id == pending.selected_recovery_code_id,
            PrivilegedRecoveryCode.user_id == actor.id,
            PrivilegedRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if selected is None:
        _deny(
            db,
            actor,
            action="privileged.recovery.reset.confirm",
            reason="pending_reset_stale",
            context=context,
            status_code=409,
        )
    now = _now()
    selected.used_at = now
    db.execute(
        update(PrivilegedRecoveryCode)
        .where(
            PrivilegedRecoveryCode.user_id == actor.id,
            PrivilegedRecoveryCode.used_at.is_(None),
        )
        .values(used_at=now)
    )
    pending_codes = list(
        db.scalars(
            select(PrivilegedRecoveryResetCode).where(
                PrivilegedRecoveryResetCode.reset_id == pending.id
            )
        )
    )
    db.add_all(
        PrivilegedRecoveryCode(
            user_id=actor.id,
            code_hash=pending_code.code_hash,
            hash_version=pending_code.hash_version,
        )
        for pending_code in pending_codes
    )
    credential.totp_secret_encrypted = pending.totp_secret_encrypted
    credential.last_totp_counter = matched
    credential.enrolled_at = now
    _advance_generation_and_revoke(db, credential, now=now)
    _delete_pending_reset(db, pending.id)
    throttle.reset(throttle_key, db=db, commit=False)
    _add_audit(
        db,
        actor=actor,
        action="privileged.recovery.reset.confirm",
        outcome="success",
        reason="credentials_replaced",
        context=context,
    )
    _commit(db)


@_atomic
def rotate_recovery_codes(
    db: Session,
    actor: User,
    settings: Settings,
    *,
    password: str,
    code: str,
    context: AuditContext,
    throttle: LoginThrottle,
    throttle_key: str,
) -> list[str]:
    retry = throttle.retry_after(throttle_key, db=db)
    if retry:
        _deny(
            db,
            actor,
            action="privileged.recovery.rotate",
            reason="locked",
            context=context,
            status_code=429,
            retry_after=retry,
        )
    valid, reason = False, "invalid_credentials"
    if verify_password(password, actor.password_hash):
        valid, reason = _verify_second_factor(db, actor, settings, code)
    if not valid:
        _deny(
            db,
            actor,
            action="privileged.recovery.rotate",
            reason=reason,
            context=context,
            status_code=409
            if reason in {"credential_unavailable", "recovery_hash_unsupported"}
            else 401,
            throttle=throttle,
            throttle_key=throttle_key,
        )
    db.execute(
        update(PrivilegedRecoveryCode)
        .where(
            PrivilegedRecoveryCode.user_id == actor.id,
            PrivilegedRecoveryCode.used_at.is_(None),
        )
        .values(used_at=_now())
    )
    credential = db.get(PrivilegedCredential, actor.id)
    if credential is None:
        _deny(
            db,
            actor,
            action="privileged.recovery.rotate",
            reason="privileged_enrollment_required",
            context=context,
            status_code=409,
        )
    _advance_generation_and_revoke(db, credential, now=_now())
    codes, recovery_rows = _new_recovery_rows(actor.id)
    db.add_all(recovery_rows)
    throttle.reset(throttle_key, db=db, commit=False)
    _add_audit(
        db,
        actor=actor,
        action="privileged.recovery.rotate",
        outcome="success",
        reason="legacy_codes_replaced",
        context=context,
    )
    _commit(db)
    return codes
