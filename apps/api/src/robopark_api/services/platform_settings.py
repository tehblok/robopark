from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import PlatformSetting

TRACKER_TOKEN_KEY = "tracker_token"
EMERGENCY_COOKIE_KEY = "emergency_cookie"
EMERGENCY_COOKIE_VALID_KEY = "emergency_cookie_valid"


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


def integration_status(db: Session) -> dict:
    tracker = get_setting(db, TRACKER_TOKEN_KEY)
    emergency = get_setting(db, EMERGENCY_COOKIE_KEY)
    valid = get_emergency_cookie_valid(db)
    return {
        "tracker_token_masked": mask_secret(tracker.value if tracker else None),
        "tracker_token_updated_at": tracker.updated_at if tracker else None,
        "emergency_cookie_masked": mask_secret(emergency.value if emergency else None),
        "emergency_cookie_updated_at": emergency.updated_at if emergency else None,
        "emergency_cookie_valid": valid,
    }
