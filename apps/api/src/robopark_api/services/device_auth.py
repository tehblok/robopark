from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException, Request
from sqlalchemy import case, delete, select, update
from sqlalchemy.orm import Session

from robopark_api.config import Settings
from robopark_api.models import AccessStatus, AgentDevice, AuthSession, DeviceAuthChallenge, User
from robopark_api.security import hash_session_token

CHALLENGE_TTL = timedelta(seconds=60)
FRESH_PASSWORD_WINDOW = timedelta(minutes=10)
MAX_PENDING_PER_DEVICE = 5
DEVICE_REQUEST_LIMIT = 12
DEVICE_REQUEST_WINDOW = timedelta(seconds=60)
AUTH_METHOD_PASSWORD = "password"
AUTH_METHOD_DEVICE = "device"


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def wire_time(value: datetime) -> str:
    return aware(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_message(
    *,
    instance_id: str,
    environment: str,
    device_id: str,
    challenge_id: str,
    nonce: str,
    expires_at: datetime,
) -> bytes:
    return (
        "robopark-device-auth-v1\n"
        f"{instance_id}\n{environment}\n{device_id}\n{challenge_id}\n{nonce}\n"
        f"{wire_time(expires_at)}"
    ).encode()


def current_session(request: Request, db: Session, settings: Settings) -> AuthSession | None:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    return db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_session_token(raw)))


def require_fresh_password_session(
    request: Request, db: Session, settings: Settings, *, user_id: int
) -> AuthSession:
    session = current_session(request, db, settings)
    if not password_session_is_fresh(session, user_id=user_id):
        raise HTTPException(status_code=403, detail="fresh_password_auth_required")
    assert session is not None
    return session


def password_session_is_fresh(session: AuthSession | None, *, user_id: int) -> bool:
    return bool(
        session is not None
        and session.user_id == user_id
        and session.auth_method == AUTH_METHOD_PASSWORD
        and datetime.now(UTC) - aware(session.created_at) <= FRESH_PASSWORD_WINDOW
    )


def enable_for_fresh_password(device: AgentDevice, *, now: datetime | None = None) -> None:
    moment = now or datetime.now(UTC)
    device.device_auth_enabled_at = moment
    device.device_auth_disabled_at = None


def disable_for_user(db: Session, user_id: int, *, now: datetime | None = None) -> None:
    moment = now or datetime.now(UTC)
    db.execute(
        update(AgentDevice)
        .where(
            AgentDevice.owner_user_id == user_id,
            AgentDevice.device_auth_enabled_at.is_not(None),
        )
        .values(device_auth_enabled_at=None, device_auth_disabled_at=moment)
    )
    db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.auth_method == AUTH_METHOD_DEVICE,
        )
    )


def disable_for_device(db: Session, device_id: str, *, now: datetime | None = None) -> None:
    moment = now or datetime.now(UTC)
    db.execute(
        update(AgentDevice)
        .where(AgentDevice.id == device_id)
        .values(device_auth_enabled_at=None, device_auth_disabled_at=moment)
    )
    db.execute(delete(AuthSession).where(AuthSession.device_id == device_id))
    db.execute(delete(DeviceAuthChallenge).where(DeviceAuthChallenge.device_id == device_id))


def charge_device_request(db: Session, device_id: str, *, now: datetime) -> None:
    """Persist the per-device request budget across API workers and processes."""
    cutoff = now - DEVICE_REQUEST_WINDOW
    reset_window = (AgentDevice.device_auth_rate_window_at.is_(None)) | (
        AgentDevice.device_auth_rate_window_at <= cutoff
    )
    charged = db.execute(
        update(AgentDevice)
        .where(AgentDevice.id == device_id)
        .values(
            device_auth_rate_window_at=case(
                (reset_window, now), else_=AgentDevice.device_auth_rate_window_at
            ),
            device_auth_rate_count=case(
                (reset_window, 1),
                (
                    AgentDevice.device_auth_rate_count < DEVICE_REQUEST_LIMIT,
                    AgentDevice.device_auth_rate_count + 1,
                ),
                else_=DEVICE_REQUEST_LIMIT + 1,
            ),
        )
        .returning(AgentDevice.device_auth_rate_count)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    db.commit()
    if charged is not None and charged > DEVICE_REQUEST_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="device_auth_rate_limited",
            headers={"Retry-After": "60"},
        )


def issue_challenge(
    db: Session,
    settings: Settings,
    *,
    device_id: str,
    nonce_hash: str,
    challenge_id: str,
    now: datetime,
) -> tuple[DeviceAuthChallenge, AgentDevice]:
    device = db.scalar(
        select(AgentDevice).where(AgentDevice.id == device_id).with_for_update(of=AgentDevice)
    )
    owner = db.get(User, device.owner_user_id) if device is not None else None
    if (
        device is None
        or owner is None
        or device.environment != settings.robopark_environment
        or device.instance_id != str(settings.robopark_instance_id)
        or device.revoked_at is not None
        or device.approved_at is None
        or device.device_auth_enabled_at is None
        or not owner.is_active
        or owner.access_status != AccessStatus.approved.value
        or owner.must_change_password
    ):
        raise HTTPException(status_code=401, detail="device_auth_unavailable")

    db.execute(
        delete(DeviceAuthChallenge).where(
            DeviceAuthChallenge.device_id == device.id,
            DeviceAuthChallenge.expires_at <= now,
        )
    )
    pending = list(
        db.scalars(
            select(DeviceAuthChallenge)
            .where(
                DeviceAuthChallenge.device_id == device.id,
                DeviceAuthChallenge.consumed_at.is_(None),
            )
            .order_by(DeviceAuthChallenge.created_at, DeviceAuthChallenge.id)
        ).all()
    )
    overflow = max(0, len(pending) - (MAX_PENDING_PER_DEVICE - 1))
    for stale in pending[:overflow]:
        stale.consumed_at = now
    challenge = DeviceAuthChallenge(
        id=challenge_id,
        device_id=device.id,
        nonce_hash=nonce_hash,
        expires_at=now + CHALLENGE_TTL,
        created_at=now,
    )
    db.add(challenge)
    db.commit()
    db.refresh(challenge)
    return challenge, device


def consume_challenge(
    db: Session,
    settings: Settings,
    *,
    device_id: str,
    challenge_id: str,
    nonce: str,
    signature: str,
    now: datetime,
) -> AgentDevice:
    device = db.scalar(
        select(AgentDevice).where(AgentDevice.id == device_id).with_for_update(of=AgentDevice)
    )
    challenge = db.scalar(
        select(DeviceAuthChallenge)
        .where(DeviceAuthChallenge.id == challenge_id)
        .with_for_update(of=DeviceAuthChallenge)
    )
    owner = db.get(User, device.owner_user_id) if device is not None else None
    if (
        challenge is None
        or device is None
        or owner is None
        or challenge.device_id != device.id
        or challenge.consumed_at is not None
        or aware(challenge.expires_at) <= now
        or device.environment != settings.robopark_environment
        or device.instance_id != str(settings.robopark_instance_id)
        or device.revoked_at is not None
        or device.approved_at is None
        or device.device_auth_enabled_at is None
        or not owner.is_active
        or owner.access_status != AccessStatus.approved.value
        or owner.must_change_password
        or not hmac.compare_digest(challenge.nonce_hash, hashlib.sha256(nonce.encode()).hexdigest())
    ):
        raise HTTPException(status_code=401, detail="device_auth_invalid")
    try:
        encoded_signature = base64.b64decode(signature, validate=True)
        public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), device.public_key_x963
        )
        public_key.verify(
            encoded_signature,
            canonical_message(
                instance_id=device.instance_id,
                environment=device.environment,
                device_id=device.id,
                challenge_id=challenge.id,
                nonce=nonce,
                expires_at=challenge.expires_at,
            ),
            ec.ECDSA(hashes.SHA256()),
        )
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise HTTPException(status_code=401, detail="device_auth_invalid") from exc

    consumed = db.execute(
        update(DeviceAuthChallenge)
        .where(
            DeviceAuthChallenge.id == challenge.id,
            DeviceAuthChallenge.device_id == device.id,
            DeviceAuthChallenge.consumed_at.is_(None),
            DeviceAuthChallenge.expires_at > now,
        )
        .values(consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=401, detail="device_auth_invalid")
    return device
