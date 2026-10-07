from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import func, select

from robopark_api.crypto import ENC_PREFIX
from robopark_api.models import (
    NativeBotControl,
    NativeBotJob,
    Park,
    PlatformSetting,
)
from robopark_api.seed_install_preset import (
    PresetImportError,
    PresetPayload,
    import_seed_preset,
    load_encrypted_preset,
)
from robopark_api.services import platform_settings


def _payload(owner: str) -> dict:
    return {
        "schema": 1,
        "owner_username": owner,
        "parks": [
            {
                "name": "Alpha park",
                "tag": "Alpha",
                "timezone": "Europe/Moscow",
                "tracker_queue": "ROBOPARK",
            }
        ],
        "legacy": {
            "locations": {
                "version": 1,
                "locations": [
                    {
                        "key": "alpha",
                        "display_name": "Alpha park",
                        "tracker_tag": "Alpha",
                        "slug": "alpha",
                        "chats": {
                            "prod": {"chat_id": -1001234567890, "thread_id": 42},
                            "test": None,
                        },
                        "participation": {"hourly_png": True},
                    }
                ],
                "status_tags": [],
                "metadata": {},
            },
            "schedules": {
                "version": 1,
                "timezone": "Europe/Moscow",
                "planner_anchor": "2026-08-07",
                "send_window": {"start_hour": 9, "end_hour": 21, "enabled": True},
                "jobs": [
                    {
                        "id": "daily",
                        "label": "Daily briefing",
                        "enabled": True,
                        "fire_at": "10:00",
                        "weekdays": [0, 1, 2, 3, 4],
                        "kind": "simple",
                        "locations": ["alpha"],
                        "text": "Briefing",
                    }
                ],
            },
            "broadcasts": {"version": 1, "campaigns": [], "removed_ids": []},
            "campaigns": {"version": 1, "campaigns": []},
        },
        "secrets": {
            "tracker_token": "tracker-secret",
            "telegram_bot_token": "telegram-secret",
        },
    }


def test_import_restores_normalized_disabled_native_configuration(db_session, seed_royal):
    result = import_seed_preset(db_session, _payload(seed_royal.username))

    assert result == {
        "already_imported": False,
        "parks": 1,
        "jobs": 2,
        "park_destinations": 1,
        "tracker_token_recovered": True,
        "telegram_bot_token_recovered": True,
        "bot_disabled": True,
    }
    park = db_session.scalar(select(Park))
    assert (park.tag, park.chat_id, park.thread_id) == ("Alpha", -1001234567890, 42)
    jobs = list(db_session.scalars(select(NativeBotJob).order_by(NativeBotJob.kind)))
    assert [(job.kind, job.enabled) for job in jobs] == [("report", False), ("text", False)]
    assert {job.timezone for job in jobs} == {"Europe/Moscow"}
    assert db_session.get(NativeBotControl, 1).deliveries_paused is True

    tracker = db_session.get(PlatformSetting, platform_settings.TRACKER_TOKEN_KEY)
    telegram = db_session.get(PlatformSetting, platform_settings.TELEGRAM_BOT_TOKEN_KEY)
    assert tracker.value.startswith(ENC_PREFIX) and "tracker-secret" not in tracker.value
    assert telegram.value.startswith(ENC_PREFIX) and "telegram-secret" not in telegram.value
    assert platform_settings.get_tracker_token(db_session) == "tracker-secret"
    assert platform_settings.get_telegram_bot_token(db_session) == "telegram-secret"


def test_import_is_idempotent_only_for_same_payload(db_session, seed_royal):
    payload = _payload(seed_royal.username)
    first = import_seed_preset(db_session, payload)
    second = import_seed_preset(db_session, payload)

    assert first["already_imported"] is False
    assert second == {**first, "already_imported": True}
    assert db_session.scalar(select(func.count()).select_from(Park)) == 1
    assert db_session.scalar(select(func.count()).select_from(NativeBotJob)) == 2


def test_import_normalizes_legacy_broadcast_without_scope_or_label(db_session, seed_royal):
    payload = _payload(seed_royal.username)
    payload["legacy"]["broadcasts"]["campaigns"] = [
        {
            "id": "legacy-default-scope",
            "text": "Legacy message",
            "enabled": True,
            "fire_at": "12:00",
            "repeat": "daily",
        }
    ]

    result = import_seed_preset(db_session, payload)

    assert result["jobs"] == 3
    broadcast = db_session.scalar(
        select(NativeBotJob).where(NativeBotJob.title == "Legacy message")
    )
    assert broadcast is not None
    assert broadcast.kind == "text"
    assert broadcast.enabled is False


def test_import_never_overwrites_existing_install(db_session, seed_royal, seed_park_with_tracker):
    original_tag = seed_park_with_tracker.tag

    with pytest.raises(PresetImportError, match="preset_target_not_empty"):
        import_seed_preset(db_session, _payload(seed_royal.username))

    assert db_session.scalar(select(func.count()).select_from(Park)) == 1
    assert db_session.scalar(select(Park.tag)) == original_tag
    assert db_session.get(PlatformSetting, platform_settings.TRACKER_TOKEN_KEY) is None


def test_invalid_legacy_rolls_back_everything(db_session, seed_royal):
    payload = _payload(seed_royal.username)
    payload["legacy"]["locations"]["locations"][0]["tracker_tag"] = "Missing"

    with pytest.raises(PresetImportError, match="preset_legacy_conflict"):
        import_seed_preset(db_session, payload)

    assert db_session.scalar(select(func.count()).select_from(Park)) == 0
    assert db_session.scalar(select(func.count()).select_from(NativeBotJob)) == 0
    assert db_session.get(PlatformSetting, platform_settings.TRACKER_TOKEN_KEY) is None


def test_payload_rejects_extra_fields_duplicate_parks_and_invalid_timezone(seed_royal):
    payload = _payload(seed_royal.username)
    payload["extra"] = True
    with pytest.raises(ValidationError):
        PresetPayload.model_validate(payload)

    payload = _payload(seed_royal.username)
    payload["parks"].append(dict(payload["parks"][0]))
    with pytest.raises(ValidationError):
        PresetPayload.model_validate(payload)

    payload = _payload(seed_royal.username)
    payload["parks"][0]["timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError):
        PresetPayload.model_validate(payload)


def test_encrypted_preset_rejects_wrong_key_and_unsafe_files(tmp_path: Path, seed_royal):
    key = Fernet.generate_key()
    key_path = tmp_path / "preset-key"
    cipher_path = tmp_path / "preset.enc"
    key_path.write_bytes(key)
    cipher_path.write_bytes(Fernet(key).encrypt(json.dumps(_payload(seed_royal.username)).encode()))
    os.chmod(key_path, 0o600)
    os.chmod(cipher_path, 0o644)

    loaded = load_encrypted_preset(key_path, cipher_path)
    assert loaded.owner_username == seed_royal.username

    key_path.write_bytes(Fernet.generate_key())
    with pytest.raises(PresetImportError, match="preset_decryption_failed"):
        load_encrypted_preset(key_path, cipher_path)

    os.chmod(key_path, 0o644)
    with pytest.raises(PresetImportError, match="unsafe_preset_file"):
        load_encrypted_preset(key_path, cipher_path)

    os.chmod(key_path, 0o600)
    os.chmod(cipher_path, 0o666)
    with pytest.raises(PresetImportError, match="unsafe_preset_file"):
        load_encrypted_preset(key_path, cipher_path)
