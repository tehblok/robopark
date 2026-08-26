from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.crypto import (
    SecretDecryptionError,
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
)
from robopark_api.models import PlatformSetting

logger = logging.getLogger(__name__)

#: Settings whose values are encrypted at rest.
SECRET_KEYS = frozenset({"tracker_token", "emergency_cookie"})

TRACKER_TOKEN_KEY = "tracker_token"
# Legacy cloud-org keys kept for DB compatibility; unused for internal Startrek.
TRACKER_ORG_ID_KEY = "tracker_org_id"
TRACKER_ORG_MODE_KEY = "tracker_org_mode"
EMERGENCY_COOKIE_KEY = "emergency_cookie"
EMERGENCY_COOKIE_VALID_KEY = "emergency_cookie_valid"
EMERGENCY_KEEPALIVE_RING_KEY = "emergency_keepalive_ring"
EMERGENCY_KEEPALIVE_SEED_VIN_KEY = "emergency_keepalive_seed_vin"
EMERGENCY_KEEPALIVE_LAST_OK_KEY = "emergency_keepalive_last_ok_at"
EMERGENCY_KEEPALIVE_RING_MAX_SIZE = 20
TRACKER_OPERATOR_UNTAGGED_KEY = "tracker_operator_untagged"
TRACKER_OPERATOR_RAW_KEY = "tracker_operator_raw"
TRACKER_OPERATOR_FIRMWARE_KEY = "tracker_operator_firmware_profile"
TRACKER_MECHANIC_WRITE_KEY = "tracker_mechanic_write"

_keepalive_ring_lock = threading.Lock()


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return "*" * (len(value) - 4) + value[-4:]


def _secret_key() -> str | None:
    return get_settings().secret_key


def get_setting(db: Session, key: str) -> PlatformSetting | None:
    return db.get(PlatformSetting, key)


def set_setting(db: Session, key: str, value: str) -> PlatformSetting:
    """Persist a setting, encrypting it when the key holds a secret."""
    stored = encrypt_secret(value, _secret_key()) if key in SECRET_KEYS else value
    row = db.get(PlatformSetting, key)
    now = datetime.now(UTC)
    if row is None:
        row = PlatformSetting(key=key, value=stored, updated_at=now)
        db.add(row)
    else:
        row.value = stored
        row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


def get_secret_setting(db: Session, key: str) -> str | None:
    """Read and decrypt a secret setting; returns None when unreadable."""
    row = get_setting(db, key)
    if row is None:
        return None
    try:
        return decrypt_secret(row.value, _secret_key())
    except SecretDecryptionError:
        logger.error(
            "Cannot decrypt setting %r — SECRET_KEY is missing or was rotated. "
            "Re-enter the value in /admin.",
            key,
        )
        return None


def migrate_plaintext_secrets(db: Session) -> int:
    """Re-seal secrets that were saved before encryption was enabled.

    When ``SECRET_KEY`` is now configured but an older row is still stored as
    plaintext, rewrite it so the DB no longer contains a working token in the
    clear. Without a key we silently no-op — the app still boots to let an
    operator upgrade in place. Returns the count of rows that were rewritten.
    """
    if not _secret_key():
        return 0
    rewritten = 0
    for setting_key in SECRET_KEYS:
        row = get_setting(db, setting_key)
        if row is None or not row.value or is_encrypted(row.value):
            continue
        # ``set_setting`` encrypts before writing back.
        set_setting(db, setting_key, row.value)
        rewritten += 1
    if rewritten:
        logger.warning(
            "Re-encrypted %d legacy plaintext secret(s) at boot; cycle SECRET_KEY only after backup.",
            rewritten,
        )
    return rewritten


def get_tracker_token(db: Session) -> str | None:
    return get_secret_setting(db, TRACKER_TOKEN_KEY)


def get_emergency_cookie(db: Session) -> str | None:
    return get_secret_setting(db, EMERGENCY_COOKIE_KEY)


def get_emergency_cookie_valid(db: Session) -> bool | None:
    row = get_setting(db, EMERGENCY_COOKIE_VALID_KEY)
    if row is None:
        return None
    return row.value.lower() in {"1", "true", "yes"}


def set_emergency_cookie_valid(db: Session, valid: bool) -> None:
    set_setting(db, EMERGENCY_COOKIE_VALID_KEY, "true" if valid else "false")


def get_keepalive_ring(db: Session) -> list[str]:
    row = get_setting(db, EMERGENCY_KEEPALIVE_RING_KEY)
    if row is None:
        return []
    try:
        ring = json.loads(row.value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(ring, list):
        return []
    return [vin for vin in ring if isinstance(vin, str)]


def touch_keepalive_ring(db: Session, vin: str) -> None:
    with _keepalive_ring_lock:
        ring = [saved_vin for saved_vin in get_keepalive_ring(db) if saved_vin != vin]
        ring.append(vin)
        set_setting(
            db,
            EMERGENCY_KEEPALIVE_RING_KEY,
            json.dumps(ring[-EMERGENCY_KEEPALIVE_RING_MAX_SIZE:]),
        )


def get_bool_setting(db: Session, key: str, default: bool) -> bool:
    row = get_setting(db, key)
    if row is None:
        return default
    return row.value.lower() in {"1", "true", "yes"}


def set_bool_setting(db: Session, key: str, value: bool) -> None:
    set_setting(db, key, "true" if value else "false")


def integration_status(db: Session) -> dict:
    tracker = get_setting(db, TRACKER_TOKEN_KEY)
    emergency = get_setting(db, EMERGENCY_COOKIE_KEY)
    valid = get_emergency_cookie_valid(db)
    # Mask the plaintext value, never the ciphertext: masking a Fernet token
    # would leak its length and tail instead of the secret's real shape.
    return {
        "tracker_token_masked": mask_secret(get_tracker_token(db)),
        "tracker_token_updated_at": tracker.updated_at if tracker else None,
        "tracker_token_encrypted": is_encrypted(tracker.value) if tracker else False,
        "emergency_cookie_masked": mask_secret(get_emergency_cookie(db)),
        "emergency_cookie_updated_at": emergency.updated_at if emergency else None,
        "emergency_cookie_encrypted": is_encrypted(emergency.value) if emergency else False,
        "emergency_cookie_valid": valid,
    }


def tracker_policy_status(db: Session) -> dict[str, bool]:
    return {
        "operator_show_untagged": get_bool_setting(db, TRACKER_OPERATOR_UNTAGGED_KEY, True),
        "operator_show_raw": get_bool_setting(db, TRACKER_OPERATOR_RAW_KEY, True),
        "operator_show_firmware_profile": get_bool_setting(db, TRACKER_OPERATOR_FIRMWARE_KEY, True),
        "mechanic_can_write": get_bool_setting(db, TRACKER_MECHANIC_WRITE_KEY, True),
    }
