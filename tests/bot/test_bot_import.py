from __future__ import annotations

import hashlib
import json
import stat
import warnings
import zipfile
from pathlib import Path

import pytest
from robopark_api.services.bot_import import (
    EXPECTED_DATA_FILES,
    BotImportError,
    import_bot_export,
    preview_bot_export,
)

ROOT = "tracker-report-snapshot"


def _data_payloads() -> dict[str, bytes]:
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
        "role_definitions": {
            "mechanic": {"label": "Mechanic", "permissions": []},
            "operator": {"label": "Operator", "permissions": ["global_search"]},
        },
    }
    users = {
        "123": {
            "role": "mechanic",
            "access": "location",
            "allowed_tags": ["Next"],
        }
    }
    payloads = {
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
    return {
        name: json.dumps(payloads[name], separators=(",", ":")).encode()
        for name in EXPECTED_DATA_FILES
    }


def _write_export(
    path: Path,
    *,
    payloads: dict[str, bytes] | None = None,
    included_data: list[str] | None = None,
    extra_members: dict[str, bytes] | None = None,
    omit: set[str] | None = None,
) -> dict[str, bytes]:
    files = payloads or _data_payloads()
    manifest = {
        "kind": "working-bot-snapshot",
        "included_data": included_data or list(EXPECTED_DATA_FILES),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{ROOT}/EXPORT_MANIFEST.json", json.dumps(manifest))
        for name, raw in files.items():
            if name not in (omit or set()):
                archive.writestr(f"{ROOT}/data/{name}", raw)
        for name, raw in (extra_members or {}).items():
            archive.writestr(name, raw)
    return files


def test_preview_reports_only_safe_file_metadata(tmp_path: Path) -> None:
    archive_path = tmp_path / "bot.zip"
    payloads = _write_export(
        archive_path,
        extra_members={
            f"{ROOT}/app/dispatcher_bot.py": b"must not be imported",
            f"{ROOT}/secrets.env": b"must not be imported",
        },
    )

    report = preview_bot_export(archive_path)

    assert report.total_bytes == sum(map(len, payloads.values()))
    assert tuple(item.filename for item in report.files) == EXPECTED_DATA_FILES
    assert report.files[0].byte_count == len(payloads[EXPECTED_DATA_FILES[0]])
    assert (
        report.files[0].sha256
        == hashlib.sha256(payloads[EXPECTED_DATA_FILES[0]]).hexdigest()
    )
    assert not hasattr(report.files[0], "content")


def test_import_preserves_raw_json_and_ignores_code_and_secrets(tmp_path: Path) -> None:
    archive_path = tmp_path / "bot.zip"
    destination = tmp_path / "data"
    payloads = _data_payloads()
    locations = json.loads(payloads["locations.json"])
    locations["spacing_marker"] = True
    payloads["locations.json"] = (
        json.dumps(locations, ensure_ascii=False, indent=2) + "\n"
    ).encode()
    _write_export(
        archive_path,
        payloads=payloads,
        extra_members={
            f"{ROOT}/app/dispatcher_bot.py": b"ignored",
            f"{ROOT}/secrets.env": b"ignored",
        },
    )

    report = import_bot_export(archive_path, destination)

    assert {path.name for path in destination.iterdir()} == set(EXPECTED_DATA_FILES)
    assert all(
        (destination / name).read_bytes() == raw for name, raw in payloads.items()
    )
    assert report == preview_bot_export(archive_path)


def test_import_never_overwrites_or_partially_imports_existing_data(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "bot.zip"
    destination = tmp_path / "data"
    destination.mkdir()
    existing_name = EXPECTED_DATA_FILES[-1]
    existing = destination / existing_name
    existing.write_bytes(b"existing")
    _write_export(archive_path)

    with pytest.raises(BotImportError, match="destination_not_empty"):
        import_bot_export(archive_path, destination)

    assert existing.read_bytes() == b"existing"
    assert {path.name for path in destination.iterdir()} == {existing_name}


def test_import_rejects_destination_symlink(tmp_path: Path) -> None:
    archive_path = tmp_path / "bot.zip"
    real_destination = tmp_path / "real-data"
    real_destination.mkdir()
    destination = tmp_path / "data"
    destination.symlink_to(real_destination, target_is_directory=True)
    _write_export(archive_path)

    with pytest.raises(BotImportError, match="destination_symlink"):
        import_bot_export(archive_path, destination)

    assert list(real_destination.iterdir()) == []


def test_import_rejects_symlinked_destination_parent(tmp_path: Path) -> None:
    archive_path = tmp_path / "bot.zip"
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    _write_export(archive_path)

    with pytest.raises(BotImportError, match="destination_symlink"):
        import_bot_export(archive_path, linked_parent / "data")

    assert list(real_parent.iterdir()) == []


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        (
            "missing_manifest",
            lambda path: zipfile.ZipFile(path, "w").close(),
        ),
        (
            "missing_data_file",
            lambda path: _write_export(path, omit={EXPECTED_DATA_FILES[0]}),
        ),
        (
            "unexpected_data_file",
            lambda path: _write_export(
                path,
                extra_members={f"{ROOT}/data/unexpected.json": b"{}"},
            ),
        ),
        (
            "unexpected_manifest_file_set",
            lambda path: _write_export(
                path,
                included_data=[*EXPECTED_DATA_FILES[:-1], "unexpected.json"],
            ),
        ),
        (
            "malformed_json",
            lambda path: _write_export(
                path,
                payloads={**_data_payloads(), EXPECTED_DATA_FILES[0]: b"{"},
            ),
        ),
    ],
)
def test_preview_rejects_invalid_export(
    tmp_path: Path,
    case: str,
    mutate: object,
) -> None:
    archive_path = tmp_path / f"{case}.zip"
    mutate(archive_path)  # type: ignore[operator]

    with pytest.raises(BotImportError, match=case):
        preview_bot_export(archive_path)


def test_preview_rejects_path_traversal_anywhere_in_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "bot.zip"
    _write_export(archive_path)
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr(f"{ROOT}/../outside.txt", b"unsafe")

    with pytest.raises(BotImportError, match="unsafe_archive_path"):
        preview_bot_export(archive_path)


def test_preview_rejects_symlink_anywhere_in_archive(tmp_path: Path) -> None:
    archive_path = tmp_path / "bot.zip"
    _write_export(archive_path)
    link = zipfile.ZipInfo(f"{ROOT}/app/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr(link, b"../../outside")

    with pytest.raises(BotImportError, match="archive_symlink"):
        preview_bot_export(archive_path)


def test_preview_rejects_duplicate_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "bot.zip"
    _write_export(archive_path)
    duplicate = f"{ROOT}/data/{EXPECTED_DATA_FILES[0]}"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive_path, "a") as archive:
            archive.writestr(duplicate, b"{}")

    with pytest.raises(BotImportError, match="duplicate_archive_member"):
        preview_bot_export(archive_path)


def test_preview_rejects_oversized_data_before_reading(tmp_path: Path) -> None:
    archive_path = tmp_path / "bot.zip"
    payloads = _data_payloads()
    payloads[EXPECTED_DATA_FILES[0]] = b" " * 65
    _write_export(archive_path, payloads=payloads)

    with pytest.raises(BotImportError, match="data_file_too_large"):
        preview_bot_export(archive_path, max_file_bytes=64)


@pytest.mark.parametrize(
    ("filename", "invalid_payload"),
    [
        ("locations.json", {"version": 1, "locations": []}),
        ("locations.sidecar.json", {"locations": [{"key": ""}]}),
        ("roles.json", {"admin_user_ids": "123", "role_definitions": {}}),
        ("roles.sidecar.json", {"admin_user_ids": [], "role_definitions": []}),
        ("dispatcher_users.json", {"not-a-telegram-id": {"role": "mechanic"}}),
        (
            "dispatcher_users.sidecar.json",
            {"users": {"123": {"role": "unknown", "allowed_tags": []}}},
        ),
        ("broadcasts.json", {"campaigns": [None], "removed_ids": []}),
        (
            "schedules.json",
            {
                "timezone": "Invalid/Timezone",
                "planner_anchor": "2026-08-07",
                "send_window": {"start_hour": 9, "end_hour": 21, "enabled": True},
                "jobs": [],
            },
        ),
    ],
)
def test_preview_rejects_json_that_legacy_store_would_reset_or_ignore(
    tmp_path: Path,
    filename: str,
    invalid_payload: object,
) -> None:
    archive_path = tmp_path / "bot.zip"
    payloads = _data_payloads()
    payloads[filename] = json.dumps(invalid_payload).encode()
    _write_export(archive_path, payloads=payloads)

    with pytest.raises(BotImportError, match=f"invalid_data_schema:{filename}"):
        preview_bot_export(archive_path)


def test_preview_rejects_stale_sidecar_that_could_restore_different_users(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "bot.zip"
    payloads = _data_payloads()
    payloads["dispatcher_users.sidecar.json"] = json.dumps(
        {
            "version": 1,
            "users": {
                "456": {
                    "role": "operator",
                    "access": "global",
                    "allowed_tags": "*",
                }
            },
        }
    ).encode()
    _write_export(archive_path, payloads=payloads)

    with pytest.raises(BotImportError, match="inconsistent_data_pair:dispatcher_users"):
        preview_bot_export(archive_path)
