from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from robopark_api.models import PlatformSetting

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


def get_setting(db: Session, key: str) -> PlatformSetting | None:
    return db.get(PlatformSetting, key)


def set_setting(db: Session, key: str, value: str) -> PlatformSetting:
    row = db.get(PlatformSetting, key)
    now = datetime.now(timezone.utc)
    if row is None:
        row = PlatformSetting(key=key, value=value, updated_at=now)
        db.add(row)
    else:
        row.value = value
        row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


def get_tracker_token(db: Session) -> str | None:
    row = get_setting(db, TRACKER_TOKEN_KEY)
    return row.value if row else None


def get_tracker_org_id(db: Session) -> str | None:
    """Deprecated for internal Startrek; kept for API shape compatibility."""
    row = get_setting(db, TRACKER_ORG_ID_KEY)
    value = (row.value if row else "") or ""
    value = value.strip()
    return value or None


def get_tracker_org_mode(db: Session) -> str:
    """Deprecated for internal Startrek; kept for API shape compatibility."""
    row = get_setting(db, TRACKER_ORG_MODE_KEY)
    mode = (row.value if row else "internal") or "internal"
    mode = mode.strip().lower()
    if mode in {"360", "org", "x-org-id"}:
        return "360"
    if mode in {"cloud", "x-cloud-org-id"}:
        return "cloud"
    return "internal"


def apply_tracker_org_context(db: Session) -> None:
    """No-op for internal Startrek (OAuth only, no org headers)."""
    return None


def get_emergency_cookie(db: Session) -> str | None:
    row = get_setting(db, EMERGENCY_COOKIE_KEY)
    return row.value if row else None


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
            json.dumps(ring[-EMERGENCY_KEEPALIVE_RING_MAX_SIZE :]),
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
    return {
        "tracker_token_masked": mask_secret(tracker.value if tracker else None),
        "tracker_token_updated_at": tracker.updated_at if tracker else None,
        "tracker_org_id": get_tracker_org_id(db),
        "tracker_org_mode": get_tracker_org_mode(db),
        "emergency_cookie_masked": mask_secret(emergency.value if emergency else None),
        "emergency_cookie_updated_at": emergency.updated_at if emergency else None,
        "emergency_cookie_valid": valid,
    }


def tracker_policy_status(db: Session) -> dict[str, bool]:
    return {
        "operator_show_untagged": get_bool_setting(db, TRACKER_OPERATOR_UNTAGGED_KEY, True),
        "operator_show_raw": get_bool_setting(db, TRACKER_OPERATOR_RAW_KEY, True),
        "operator_show_firmware_profile": get_bool_setting(
            db, TRACKER_OPERATOR_FIRMWARE_KEY, True
        ),
        "mechanic_can_write": get_bool_setting(db, TRACKER_MECHANIC_WRITE_KEY, True),
    }
