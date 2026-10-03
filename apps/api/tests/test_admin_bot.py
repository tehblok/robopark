from __future__ import annotations

import json
import os
import secrets
import threading
import zipfile
from pathlib import Path

from conftest import login_as
from robopark_api.crypto import is_encrypted
from robopark_api.models import AuditLog
from robopark_api.services import bot_import, bot_settings, platform_settings
from robopark_api.services.bot_import import EXPECTED_DATA_FILES


def _telegram_token() -> str:
    return f"100000:{secrets.token_urlsafe(24)}"


def _configure_bridge(monkeypatch, tmp_path) -> tuple[str, dict[str, str]]:
    key = secrets.token_urlsafe(32)
    key_path = tmp_path / "bot-bridge-key"
    key_path.write_text(key, encoding="utf-8")
    key_path.chmod(0o600)
    monkeypatch.setenv("ROBOPARK_BOT_BRIDGE_KEY_FILE", str(key_path))
    return key, {"X-Robopark-Bot-Key": key}


def _bot_export(path: Path, *, overrides: dict[str, object] | None = None) -> dict[str, bytes]:
    location = {
        "key": "Next",
        "display_name": "Next",
        "tracker_tag": "Next",
        "slug": "next",
        "chats": {"prod": None, "test": None},
        "logistics": None,
        "participation": {},
    }
    roles = {
        "version": 1,
        "admin_user_ids": [123],
        "role_definitions": {"mechanic": {"label": "Mechanic", "permissions": []}},
    }
    users = {
        "123": {
            "role": "mechanic",
            "access": "location",
            "allowed_tags": ["Next"],
        }
    }
    raw_payloads = {
        "locations.json": {"version": 1, "locations": [location]},
        "locations.sidecar.json": {"locations": [location]},
        "roles.json": roles,
        "roles.sidecar.json": roles,
        "dispatcher_users.json": users,
        "dispatcher_users.sidecar.json": {"version": 1, "users": users},
        "broadcasts.json": {"version": 1, "campaigns": [], "removed_ids": []},
        "schedules.json": {
            "timezone": "Europe/Moscow",
            "planner_anchor": "2026-08-07",
            "send_window": {"start_hour": 9, "end_hour": 21, "enabled": True},
            "jobs": [],
        },
    }
    raw_payloads.update(overrides or {})
    payloads = {name: json.dumps(raw_payloads[name]).encode() for name in EXPECTED_DATA_FILES}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "tracker-report-snapshot/EXPORT_MANIFEST.json",
            json.dumps(
                {
                    "kind": "working-bot-snapshot",
                    "included_data": list(EXPECTED_DATA_FILES),
                }
            ),
        )
        for name, raw in payloads.items():
            archive.writestr(f"tracker-report-snapshot/data/{name}", raw)
        archive.writestr("tracker-report-snapshot/secrets.env", b"ignored")
    return payloads


def test_royal_can_store_masked_telegram_token(
    client, db_session, seed_royal, test_settings, tmp_path, monkeypatch
):
    token = _telegram_token()
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    login_as(client, "royal", "secret")

    saved = client.put("/admin/bot/token", json={"token": token})

    assert saved.status_code == 200
    assert saved.json() == {
        "desired_enabled": False,
        "runtime_state": "stopped",
        "token_configured": True,
        "token_masked": f"•••• ({len(token)})",
        "token_updated_at": saved.json()["token_updated_at"],
        "token_encrypted": True,
    }
    assert token not in saved.text
    stored = platform_settings.get_setting(db_session, platform_settings.TELEGRAM_BOT_TOKEN_KEY)
    assert stored is not None
    assert token not in stored.value
    assert is_encrypted(stored.value)


def test_enable_disable_writes_exact_atomic_host_contract(
    client, db_session, seed_royal, test_settings, tmp_path, monkeypatch
):
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    platform_settings.set_telegram_bot_token(db_session, _telegram_token())
    login_as(client, "royal", "secret")

    enable = client.put("/admin/bot/enabled", json={"enabled": True})
    control = host_data / "telegram-bot" / "enabled.json"

    assert enable.status_code == 200
    assert enable.json()["desired_enabled"] is True
    assert control.read_bytes() == b'{"schema":1,"enabled":true}\n'
    assert control.stat().st_mode & 0o777 == 0o600
    assert not list(control.parent.glob(".enabled.*.tmp"))

    disable = client.put("/admin/bot/enabled", json={"enabled": False})

    assert disable.status_code == 200
    assert disable.json()["desired_enabled"] is False
    assert control.read_bytes() == b'{"schema":1,"enabled":false}\n'
    status = client.get("/admin/bot")
    assert status.status_code == 200
    assert status.json()["desired_enabled"] is False
    assert status.json()["runtime_state"] == "stopped"


def test_status_reports_fresh_bot_heartbeat_and_stale_failure(
    client, db_session, seed_royal, test_settings, tmp_path, monkeypatch
):
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    platform_settings.set_telegram_bot_token(db_session, _telegram_token())
    login_as(client, "royal", "secret")
    assert client.put("/admin/bot/enabled", json={"enabled": True}).status_code == 200
    heartbeat = host_data / "telegram-bot" / "runtime-heartbeat"
    heartbeat.write_bytes(b"1\n")
    heartbeat.chmod(0o600)

    running = client.get("/admin/bot")
    os.utime(heartbeat, (1, 1))
    os.utime(host_data / "telegram-bot" / "enabled.json", (1, 1))
    stale = client.get("/admin/bot")

    assert running.json()["runtime_state"] == "running"
    assert stale.json()["runtime_state"] == "unavailable"


def test_telegram_token_cannot_change_while_bot_is_running(
    client, db_session, seed_royal, test_settings, tmp_path, monkeypatch
):
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    original = _telegram_token()
    replacement = _telegram_token()
    platform_settings.set_telegram_bot_token(db_session, original)
    login_as(client, "royal", "secret")
    assert client.put("/admin/bot/enabled", json={"enabled": True}).status_code == 200

    response = client.put("/admin/bot/token", json={"token": replacement})

    assert response.status_code == 409
    assert response.json() == {"detail": "telegram_bot_disable_before_rotation"}
    assert platform_settings.get_telegram_bot_token(db_session) == original


def test_enabling_requires_telegram_token_and_does_not_create_control_file(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    login_as(client, "royal", "secret")

    response = client.put("/admin/bot/enabled", json={"enabled": True})

    assert response.status_code == 409
    assert response.json() == {"detail": "telegram_bot_token_required"}
    assert not (host_data / "telegram-bot" / "enabled.json").exists()


def test_bot_control_rejects_symlinked_service_directory(
    client, db_session, seed_royal, test_settings, tmp_path, monkeypatch
):
    host_data = tmp_path / "host-data"
    outside = tmp_path / "outside"
    host_data.mkdir()
    outside.mkdir()
    (host_data / "telegram-bot").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    platform_settings.set_telegram_bot_token(db_session, _telegram_token())
    login_as(client, "royal", "secret")

    response = client.put("/admin/bot/enabled", json={"enabled": True})

    assert response.status_code == 503
    assert response.json() == {"detail": "bot_host_control_unavailable"}
    assert list(outside.iterdir()) == []


def test_bot_status_rejects_control_file_with_unsafe_permissions(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    control_dir = tmp_path / "host-data" / "telegram-bot"
    control_dir.mkdir(parents=True)
    control = control_dir / "enabled.json"
    control.write_bytes(b'{"schema":1,"enabled":true}\n')
    control.chmod(0o644)
    monkeypatch.setattr(test_settings, "host_data_path", str(tmp_path / "host-data"))
    login_as(client, "royal", "secret")

    response = client.get("/admin/bot")

    assert response.status_code == 503
    assert response.json() == {"detail": "bot_host_control_state_invalid"}


def test_bot_status_rejects_duplicate_control_keys_like_host(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    control_dir = tmp_path / "host-data" / "telegram-bot"
    control_dir.mkdir(parents=True)
    control = control_dir / "enabled.json"
    control.write_bytes(b'{"schema":1,"enabled":false,"enabled":true}\n')
    control.chmod(0o600)
    monkeypatch.setattr(test_settings, "host_data_path", str(tmp_path / "host-data"))
    login_as(client, "royal", "secret")

    response = client.get("/admin/bot")

    assert response.status_code == 503
    assert response.json() == {"detail": "bot_host_control_state_invalid"}


def test_bot_status_rejects_symlinked_control_file_without_reading_target(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    control_dir = tmp_path / "host-data" / "telegram-bot"
    control_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b'{"schema":1,"enabled":true}\n')
    outside.chmod(0o600)
    (control_dir / "enabled.json").symlink_to(outside)
    monkeypatch.setattr(test_settings, "host_data_path", str(tmp_path / "host-data"))
    login_as(client, "royal", "secret")

    response = client.get("/admin/bot")

    assert response.status_code == 503
    assert response.json() == {"detail": "bot_host_control_state_invalid"}
    assert outside.read_bytes() == b'{"schema":1,"enabled":true}\n'


def test_admin_cannot_read_or_change_bot_settings(client, seed_admin):
    login_as(client, "admin", "secret")

    assert client.get("/admin/bot").status_code == 403
    assert client.put("/admin/bot/token", json={"token": _telegram_token()}).status_code == 403
    assert client.put("/admin/bot/enabled", json={"enabled": False}).status_code == 403


def test_bot_settings_audit_never_contains_telegram_token(
    client, db_session, seed_royal, test_settings, tmp_path, monkeypatch
):
    token = _telegram_token()
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    login_as(client, "royal", "secret")

    assert client.put("/admin/bot/token", json={"token": token}).status_code == 200

    details = [
        row.detail or ""
        for row in db_session.query(AuditLog)
        .filter(AuditLog.action == "admin.settings.changed")
        .all()
    ]
    assert details == ["Telegram bot token updated"]
    assert all(token not in detail for detail in details)


def test_internal_bot_gets_token_only_with_bridge_key_and_when_enabled(
    client, db_session, seed_royal, test_settings, monkeypatch, tmp_path
):
    _key, headers = _configure_bridge(monkeypatch, tmp_path)
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    token = _telegram_token()
    platform_settings.set_telegram_bot_token(db_session, token)
    login_as(client, "royal", "secret")

    disabled = client.get("/internal/bot/telegram-token", headers=headers)
    enabled = client.put("/admin/bot/enabled", json={"enabled": True})
    missing = client.get("/internal/bot/telegram-token")
    allowed = client.get("/internal/bot/telegram-token", headers=headers)

    assert disabled.status_code == 409
    assert enabled.status_code == 200
    assert disabled.json() == {"detail": "telegram_bot_disabled"}
    assert missing.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {"token": token}
    assert allowed.headers["cache-control"] == "no-store"


def test_internal_bot_token_fails_closed_when_secret_is_missing(
    client, test_settings, monkeypatch, tmp_path
):
    _key, headers = _configure_bridge(monkeypatch, tmp_path)
    host_data = tmp_path / "host-data"
    control_dir = host_data / "telegram-bot"
    control_dir.mkdir(parents=True)
    (control_dir / "enabled.json").write_bytes(b'{"schema":1,"enabled":true}\n')
    (control_dir / "enabled.json").chmod(0o600)
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))

    response = client.get("/internal/bot/telegram-token", headers=headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "telegram_bot_token_not_configured"}


def test_royal_can_preview_bot_export_without_persisting_uploaded_zip(client, seed_royal, tmp_path):
    archive_path = tmp_path / "bot-export.zip"
    payloads = _bot_export(archive_path)
    login_as(client, "royal", "secret")

    response = client.post(
        "/admin/bot/import/preview",
        files={"archive": ("bot-export.zip", archive_path.read_bytes(), "application/zip")},
    )

    assert response.status_code == 200
    assert response.json()["total_bytes"] == sum(map(len, payloads.values()))
    assert [row["filename"] for row in response.json()["files"]] == list(EXPECTED_DATA_FILES)
    assert not (tmp_path / "telegram-bot").exists()


def test_preview_explains_legacy_data_schema_failure(client, seed_royal, tmp_path):
    archive_path = tmp_path / "bot-export.zip"
    _bot_export(
        archive_path,
        overrides={"locations.json": {"version": 1, "locations": []}},
    )
    login_as(client, "royal", "secret")

    response = client.post(
        "/admin/bot/import/preview",
        files={"archive": ("bot-export.zip", archive_path.read_bytes(), "application/zip")},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_data_schema:locations.json"}


def test_import_requires_disabled_bot_and_does_not_touch_destination(
    client, db_session, seed_royal, test_settings, tmp_path, monkeypatch
):
    archive_path = tmp_path / "bot-export.zip"
    _bot_export(archive_path)
    monkeypatch.setattr(test_settings, "host_data_path", str(tmp_path / "host-data"))
    token = _telegram_token()
    platform_settings.set_telegram_bot_token(db_session, token)
    control_dir = tmp_path / "host-data" / "telegram-bot"
    control_dir.mkdir(parents=True)
    (control_dir / "enabled.json").write_bytes(b'{"schema":1,"enabled":true}\n')
    (control_dir / "enabled.json").chmod(0o600)
    login_as(client, "royal", "secret")

    response = client.post(
        "/admin/bot/import/execute",
        files={"archive": ("bot-export.zip", archive_path.read_bytes(), "application/zip")},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "telegram_bot_must_be_disabled"}
    assert not (control_dir / "data").exists()
    assert (control_dir / "enabled.json").read_bytes() == b'{"schema":1,"enabled":true}\n'


def test_disabled_bot_imports_only_eight_data_files_and_discards_upload(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    archive_path = tmp_path / "bot-export.zip"
    payloads = _bot_export(archive_path)
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    login_as(client, "royal", "secret")

    response = client.post(
        "/admin/bot/import/execute",
        files={"archive": ("bot-export.zip", archive_path.read_bytes(), "application/zip")},
    )

    destination = host_data / "telegram-bot" / "data"
    assert response.status_code == 200
    assert {path.name for path in destination.iterdir()} == set(EXPECTED_DATA_FILES)
    assert all((destination / name).read_bytes() == raw for name, raw in payloads.items())
    assert not list(host_data.rglob("*.zip"))


def test_enable_waits_until_atomic_import_has_finished(
    client,
    db_session,
    seed_royal,
    test_settings,
    tmp_path,
    monkeypatch,
):
    archive_path = tmp_path / "bot-export.zip"
    _bot_export(archive_path)
    host_data = tmp_path / "host-data"
    host_data.mkdir()
    monkeypatch.setattr(test_settings, "host_data_path", str(host_data))
    platform_settings.set_telegram_bot_token(db_session, _telegram_token())
    login_as(client, "royal", "secret")

    import_entered = threading.Event()
    enable_entered = threading.Event()
    enable_finished = threading.Event()
    real_import = bot_import.import_bot_export
    real_enable = bot_settings.set_desired_enabled
    enable_threads: list[threading.Thread] = []

    def blocked_import(*args, **kwargs):
        import_entered.set()

        def enable_during_import() -> None:
            enable_entered.set()
            try:
                real_enable(str(host_data), True)
            finally:
                enable_finished.set()

        thread = threading.Thread(target=enable_during_import)
        enable_threads.append(thread)
        thread.start()
        assert enable_entered.wait(2)
        assert not enable_finished.wait(0.1)
        return real_import(*args, **kwargs)

    monkeypatch.setattr(bot_import, "import_bot_export", blocked_import)

    imported = client.post(
        "/admin/bot/import/execute",
        files={
            "archive": (
                "bot-export.zip",
                archive_path.read_bytes(),
                "application/zip",
            )
        },
    )
    assert import_entered.is_set()
    for thread in enable_threads:
        thread.join(timeout=2)

    assert imported.status_code == 200
    assert enable_finished.is_set()
    assert bot_settings.desired_enabled(str(host_data)) is True
