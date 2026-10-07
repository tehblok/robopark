from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.db import SessionLocal
from robopark_api.models import (
    AccessStatus,
    NativeBotControl,
    NativeBotJob,
    NativeBotMigration,
    NativeBotMigrationSource,
    Park,
    PlatformSetting,
    User,
)
from robopark_api.services import native_telegram_migration, platform_settings

DEFAULT_KEY_FILE = Path("/run/robopark/preset-key")
DEFAULT_PRESET_FILE = Path("/run/robopark/preset.enc")
MAX_KEY_BYTES = 128
MAX_PRESET_BYTES = 1024 * 1024
PRESET_FINGERPRINT_KEY = "seed_install_preset_fingerprint"


class PresetImportError(ValueError):
    pass


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PresetPark(_StrictModel):
    name: str = Field(min_length=1, max_length=128)
    tag: str = Field(min_length=1, max_length=64)
    timezone: str = Field(min_length=1, max_length=64)
    tracker_queue: str = Field(min_length=1, max_length=128)

    @field_validator("name", "tag", "timezone", "tracker_queue")
    @classmethod
    def no_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("surrounding whitespace is not allowed")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("invalid IANA timezone") from exc
        return value


class PresetLegacy(_StrictModel):
    locations: dict[str, Any]
    schedules: dict[str, Any]
    broadcasts: dict[str, Any]
    campaigns: dict[str, Any] = Field(default_factory=lambda: {"version": 1, "campaigns": []})


class PresetSecrets(_StrictModel):
    tracker_token: str = Field(min_length=1, max_length=16_384)
    telegram_bot_token: str = Field(min_length=1, max_length=16_384)


class PresetPayload(_StrictModel):
    schema_version: Literal[1] = Field(alias="schema")
    owner_username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    parks: list[PresetPark] = Field(min_length=1, max_length=500)
    legacy: PresetLegacy
    secrets: PresetSecrets

    @model_validator(mode="after")
    def unique_parks(self) -> PresetPayload:
        tags = [park.tag for park in self.parks]
        if len(tags) != len(set(tags)):
            raise ValueError("park tags must be unique")
        return self


def _canonical_fingerprint(payload: PresetPayload) -> str:
    raw = json.dumps(
        payload.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _safe_read(path: Path, *, maximum: int, private: bool) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            mode = stat.S_IMODE(info.st_mode)
            unsafe_private = private and (
                mode != 0o600 or info.st_uid != os.geteuid() or info.st_nlink != 1
            )
            if (
                not stat.S_ISREG(info.st_mode)
                or unsafe_private
                or (not private and mode & 0o022)
                or not 0 < info.st_size <= maximum
            ):
                raise PresetImportError("unsafe_preset_file")
            raw = source.read(maximum + 1)
            if len(raw) > maximum:
                raise PresetImportError("unsafe_preset_file")
            return raw
    except PresetImportError:
        raise
    except OSError as exc:
        raise PresetImportError("unsafe_preset_file") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PresetImportError("invalid_preset_payload")
        result[key] = value
    return result


def load_encrypted_preset(key_path: Path, preset_path: Path) -> PresetPayload:
    key = _safe_read(key_path, maximum=MAX_KEY_BYTES, private=True).strip()
    cipher = _safe_read(preset_path, maximum=MAX_PRESET_BYTES, private=False).strip()
    try:
        plaintext = Fernet(key).decrypt(cipher)
    except (ValueError, InvalidToken) as exc:
        raise PresetImportError("preset_decryption_failed") from exc
    try:
        value = json.loads(plaintext, object_pairs_hook=_unique_object)
        return PresetPayload.model_validate(value)
    except PresetImportError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PresetImportError("invalid_preset_payload") from exc
    finally:
        del plaintext


def _write_legacy_tree(root: Path, legacy: PresetLegacy) -> None:
    directory = root / "telegram-bot" / "data"
    directory.mkdir(parents=True, mode=0o700)
    broadcasts = json.loads(json.dumps(legacy.broadcasts))
    for row in broadcasts.get("campaigns", []):
        if isinstance(row, dict):
            if "location_mode" not in row:
                # The standalone bot treated an absent scope as hourly_png.
                row["location_mode"] = "hourly_png"
            if not str(row.get("label") or "").strip():
                # Match the standalone admin title for old custom broadcasts.
                text = str(row.get("text") or "").replace("\n", " ")
                row["label"] = text if len(text) <= 32 else f"{text[:31]}…"
    values = {
        "locations.json": legacy.locations,
        "schedules.json": legacy.schedules,
        "broadcasts.json": broadcasts,
        "sk_campaigns.json": legacy.campaigns,
    }
    for filename, value in values.items():
        path = directory / filename
        path.write_text(
            json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        path.chmod(0o600)


def _summary(db: Session, *, already_imported: bool) -> dict[str, int | bool]:
    return {
        "already_imported": already_imported,
        "parks": db.scalar(select(func.count()).select_from(Park)) or 0,
        "jobs": db.scalar(select(func.count()).select_from(NativeBotJob)) or 0,
        "park_destinations": db.scalar(
            select(func.count()).select_from(Park).where(Park.chat_id.is_not(None))
        )
        or 0,
        "tracker_token_recovered": db.get(PlatformSetting, platform_settings.TRACKER_TOKEN_KEY)
        is not None,
        "telegram_bot_token_recovered": db.get(
            PlatformSetting, platform_settings.TELEGRAM_BOT_TOKEN_KEY
        )
        is not None,
        "bot_disabled": bool(
            (control := db.get(NativeBotControl, 1)) and control.deliveries_paused
        ),
    }


def import_seed_preset(db: Session, payload: PresetPayload | dict[str, Any]) -> dict[str, Any]:
    parsed = (
        payload if isinstance(payload, PresetPayload) else PresetPayload.model_validate(payload)
    )
    fingerprint = _canonical_fingerprint(parsed)
    existing_fingerprint = db.get(PlatformSetting, PRESET_FINGERPRINT_KEY)
    if existing_fingerprint is not None:
        if existing_fingerprint.value == fingerprint:
            return _summary(db, already_imported=True)
        raise PresetImportError("preset_already_imported")

    owner = db.scalar(select(User).where(User.username == parsed.owner_username))
    if (
        owner is None
        or not owner.is_active
        or owner.access_status != AccessStatus.approved.value
        or owner.role != "royal"
    ):
        raise PresetImportError("preset_owner_invalid")
    if (
        db.scalar(select(func.count()).select_from(Park))
        or db.scalar(select(func.count()).select_from(NativeBotJob))
        or db.get(PlatformSetting, platform_settings.TRACKER_TOKEN_KEY) is not None
        or db.get(PlatformSetting, platform_settings.TELEGRAM_BOT_TOKEN_KEY) is not None
    ):
        raise PresetImportError("preset_target_not_empty")

    try:
        for item in parsed.parks:
            db.add(
                Park(
                    name=item.name,
                    tag=item.tag,
                    timezone=item.timezone,
                    tracker_queue=item.tracker_queue,
                    is_active=True,
                )
            )
        db.flush()

        temporary_root = Path(tempfile.gettempdir()).resolve()
        with tempfile.TemporaryDirectory(
            prefix="robopark-preset-", dir=temporary_root
        ) as temporary:
            root = Path(temporary)
            _write_legacy_tree(root, parsed.legacy)
            try:
                plan = native_telegram_migration.preview(db, str(root))
            except HTTPException as exc:
                raise PresetImportError("preset_legacy_invalid") from exc
        if plan["conflicts"]:
            reasons = Counter(str(item.get("reason") or "unknown") for item in plan["conflicts"])
            summary = ",".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))
            raise PresetImportError(f"preset_legacy_conflict:{summary}")

        applied_at = datetime.now(UTC)
        stored = {
            **plan,
            "already_applied": False,
            "applied_at": applied_at.isoformat(),
        }
        db.add(
            NativeBotMigration(
                fingerprint=plan["fingerprint"],
                source_fingerprint=plan["_source_fingerprint"],
                actor_user_id=owner.id,
                result_json=json.dumps(
                    stored, ensure_ascii=False, separators=(",", ":"), default=str
                ),
                created_at=applied_at,
            )
        )
        for source_ref in plan.get("_tombstones", []):
            db.add(
                NativeBotMigrationSource(
                    source_ref=source_ref,
                    fingerprint=plan["fingerprint"],
                    disposition="deleted",
                )
            )
        for update in plan["park_updates"]:
            park = db.get(Park, update["park_id"])
            if park is None or park.chat_id is not None:
                raise PresetImportError("preset_state_changed")
            park.chat_id = update["chat_id"]
            park.thread_id = update["thread_id"]
            park.bot_revision += 1
        for item in plan["jobs"]:
            job = NativeBotJob(
                source_ref=item["source_ref"],
                park_id=item["park_id"],
                kind=item["kind"],
                title=item["title"],
                enabled=False,
                schedule=item["schedule"],
                timezone=item["timezone"],
                time=item["time"],
                run_at=item["run_at"],
                start_hour=item["start_hour"],
                end_hour=item["end_hour"],
                weekdays=",".join(str(day) for day in item["weekdays"]),
                text=item["text"],
                url=item["url"],
                tracker_tag=item["tracker_tag"],
                alternate=item["alternate"],
                anchor_date=item["anchor_date"],
            )
            db.add(job)
            db.flush()
            db.add(
                NativeBotMigrationSource(
                    source_ref=item["source_ref"],
                    fingerprint=plan["fingerprint"],
                    disposition="imported",
                    native_job_id=job.id,
                )
            )

        control = db.get(NativeBotControl, 1)
        if control is None:
            control = NativeBotControl(id=1, deliveries_paused=True, revision=1)
            db.add(control)
        else:
            control.deliveries_paused = True
            control.revision += 1
        control.updated_at = applied_at
        platform_settings._upsert_setting(  # noqa: SLF001 - transaction must remain atomic
            db,
            platform_settings.TRACKER_TOKEN_KEY,
            parsed.secrets.tracker_token,
            now=applied_at,
        )
        platform_settings._upsert_setting(  # noqa: SLF001 - transaction must remain atomic
            db,
            platform_settings.TELEGRAM_BOT_TOKEN_KEY,
            parsed.secrets.telegram_bot_token,
            now=applied_at,
        )
        db.add(
            PlatformSetting(
                key=PRESET_FINGERPRINT_KEY,
                value=fingerprint,
                updated_at=applied_at,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise PresetImportError("preset_state_changed") from None
    except PresetImportError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return _summary(db, already_imported=False)


def main() -> int:
    if os.geteuid() != 0:
        raise PresetImportError("preset_requires_root")
    payload = load_encrypted_preset(DEFAULT_KEY_FILE, DEFAULT_PRESET_FILE)
    with SessionLocal() as database:
        result = import_seed_preset(database, payload)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
