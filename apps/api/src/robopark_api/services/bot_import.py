from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from os import PathLike
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EXPECTED_DATA_FILES = (
    "locations.json",
    "locations.sidecar.json",
    "roles.json",
    "roles.sidecar.json",
    "dispatcher_users.json",
    "dispatcher_users.sidecar.json",
    "broadcasts.json",
    "schedules.json",
)

_ARCHIVE_ROOT = "tracker-report-snapshot"
_MANIFEST_PATH = f"{_ARCHIVE_ROOT}/EXPORT_MANIFEST.json"
_DATA_PREFIX = f"{_ARCHIVE_ROOT}/data/"
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_ARCHIVE_MEMBERS = 4096


class BotImportError(ValueError):
    pass


@dataclass(frozen=True)
class ImportedFile:
    filename: str
    byte_count: int
    sha256: str


@dataclass(frozen=True)
class BotImportReport:
    files: tuple[ImportedFile, ...]
    total_bytes: int


@dataclass(frozen=True)
class _DestinationHandle:
    directory_fd: int
    parent_fd: int
    leaf_name: str
    created: bool


def preview_bot_export(
    archive_path: str | PathLike[str],
    *,
    max_file_bytes: int = 2 * 1024 * 1024,
    max_total_bytes: int = 8 * 1024 * 1024,
) -> BotImportReport:
    report, _ = _load_export(
        archive_path,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    return report


def import_bot_export(
    archive_path: str | PathLike[str],
    destination: str | PathLike[str],
    *,
    max_file_bytes: int = 2 * 1024 * 1024,
    max_total_bytes: int = 8 * 1024 * 1024,
) -> BotImportReport:
    report, payloads = _load_export(
        archive_path,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    destination_path = Path(destination)
    handle = _open_destination(destination_path)
    created_files: list[str] = []
    temporary_files: list[str] = []
    try:
        for filename in EXPECTED_DATA_FILES:
            temporary_name, descriptor = _create_temporary_file(handle.directory_fd, filename)
            temporary_files.append(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(payloads[filename])
                    output.flush()
                    os.fsync(output.fileno())
                os.link(
                    temporary_name,
                    filename,
                    src_dir_fd=handle.directory_fd,
                    dst_dir_fd=handle.directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise BotImportError("destination_changed") from exc
            created_files.append(filename)
            os.unlink(temporary_name, dir_fd=handle.directory_fd)
            temporary_files.remove(temporary_name)
        _verify_destination_unchanged(handle, set(EXPECTED_DATA_FILES))
        os.fsync(handle.directory_fd)
    except BotImportError:
        _rollback_import(created_files, temporary_files, handle)
        raise
    except OSError as exc:
        _rollback_import(created_files, temporary_files, handle)
        raise BotImportError("destination_write_failed") from exc
    finally:
        os.close(handle.directory_fd)
        os.close(handle.parent_fd)
    return report


def _load_export(
    archive_path: str | PathLike[str],
    *,
    max_file_bytes: int,
    max_total_bytes: int,
) -> tuple[BotImportReport, dict[str, bytes]]:
    if max_file_bytes <= 0 or max_total_bytes <= 0:
        raise BotImportError("invalid_size_limit")
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            _validate_members(members)
            member_by_name = {member.filename: member for member in members}
            manifest_info = member_by_name.get(_MANIFEST_PATH)
            if manifest_info is None or manifest_info.is_dir():
                raise BotImportError("missing_manifest")
            if manifest_info.file_size > _MAX_MANIFEST_BYTES:
                raise BotImportError("manifest_too_large")
            manifest_raw = _read_member(archive, manifest_info, _MAX_MANIFEST_BYTES)
            manifest = _parse_manifest(manifest_raw)
            if manifest.get("kind") != "working-bot-snapshot":
                raise BotImportError("unexpected_manifest_kind")
            included_data = manifest.get("included_data")
            if not isinstance(included_data, list) or tuple(included_data) != EXPECTED_DATA_FILES:
                raise BotImportError("unexpected_manifest_file_set")

            expected_paths = {_DATA_PREFIX + name for name in EXPECTED_DATA_FILES}
            actual_data_paths = {
                member.filename
                for member in members
                if member.filename.startswith(_DATA_PREFIX) and not member.is_dir()
            }
            if actual_data_paths - expected_paths:
                raise BotImportError("unexpected_data_file")
            if expected_paths - actual_data_paths:
                raise BotImportError("missing_data_file")

            total_bytes = 0
            payloads: dict[str, bytes] = {}
            parsed_payloads: dict[str, Any] = {}
            imported_files: list[ImportedFile] = []
            for filename in EXPECTED_DATA_FILES:
                info = member_by_name[_DATA_PREFIX + filename]
                if info.file_size > max_file_bytes:
                    raise BotImportError("data_file_too_large")
                total_bytes += info.file_size
                if total_bytes > max_total_bytes:
                    raise BotImportError("data_total_too_large")
                raw = _read_member(archive, info, max_file_bytes)
                parsed_payloads[filename] = _parse_data_file(raw, filename)
                payloads[filename] = raw
                imported_files.append(
                    ImportedFile(
                        filename=filename,
                        byte_count=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest(),
                    )
                )
            _validate_data_pairs(parsed_payloads)
    except BotImportError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise BotImportError("invalid_archive") from exc

    return BotImportReport(files=tuple(imported_files), total_bytes=total_bytes), payloads


def _parse_data_file(raw: bytes, filename: str) -> Any:
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_data_duplicate_keys)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BotImportError("malformed_json") from exc
    try:
        if filename in {"locations.json", "locations.sidecar.json"}:
            _validate_locations(payload)
        elif filename in {"roles.json", "roles.sidecar.json"}:
            _validate_roles(payload)
        elif filename in {"dispatcher_users.json", "dispatcher_users.sidecar.json"}:
            _validate_dispatcher_users(payload, sidecar=filename.endswith("sidecar.json"))
        elif filename == "broadcasts.json":
            _validate_broadcasts(payload)
        elif filename == "schedules.json":
            _validate_schedules(payload)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise BotImportError(f"invalid_data_schema:{filename}") from exc
    return payload


def _reject_data_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BotImportError("duplicate_json_key")
        result[key] = value
    return result


def _require_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError
    return value


def _require_nonempty_string(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError
    return value


def _validate_locations(payload: Any) -> None:
    data = _require_dict(payload)
    locations = data.get("locations")
    if not isinstance(locations, list) or not locations:
        raise ValueError
    keys: set[str] = set()
    for raw_location in locations:
        location = _require_dict(raw_location)
        key = _require_nonempty_string(location.get("key"))
        if key in keys:
            raise ValueError
        keys.add(key)
        for field in ("display_name", "tracker_tag", "slug"):
            _require_nonempty_string(location.get(field))
        chats = _require_dict(location.get("chats"))
        for profile in ("prod", "test"):
            chat = chats.get(profile)
            if chat is None:
                continue
            chat_data = _require_dict(chat)
            if type(chat_data.get("chat_id")) is not int:
                raise ValueError
            thread_id = chat_data.get("thread_id")
            if thread_id is not None and type(thread_id) is not int:
                raise ValueError
        if not isinstance(location.get("participation"), dict):
            raise ValueError


def _validate_roles(payload: Any) -> None:
    data = _require_dict(payload)
    admin_ids = data.get("admin_user_ids")
    definitions = data.get("role_definitions")
    if not isinstance(admin_ids, list) or not isinstance(definitions, dict):
        raise ValueError
    if any(type(user_id) is not int for user_id in admin_ids):
        raise ValueError
    for name, raw_spec in definitions.items():
        _require_nonempty_string(name)
        spec = _require_dict(raw_spec)
        _require_nonempty_string(spec.get("label"))
        permissions = spec.get("permissions")
        if not isinstance(permissions, list) or not all(
            isinstance(permission, str) for permission in permissions
        ):
            raise ValueError


def _validate_dispatcher_users(payload: Any, *, sidecar: bool) -> None:
    data = _require_dict(payload)
    users = data.get("users") if sidecar else data
    users = _require_dict(users)
    for raw_user_id, raw_profile in users.items():
        user_id = _require_nonempty_string(raw_user_id)
        if not user_id.isdecimal() or int(user_id) <= 0:
            raise ValueError
        profile = _require_dict(raw_profile)
        if profile.get("role") not in {"mechanic", "operator"}:
            raise ValueError
        allowed_tags = profile.get("allowed_tags")
        if allowed_tags != "*" and not (
            isinstance(allowed_tags, list)
            and all(isinstance(tag, str) and tag.strip() for tag in allowed_tags)
        ):
            raise ValueError


def _validate_broadcasts(payload: Any) -> None:
    data = _require_dict(payload)
    campaigns = data.get("campaigns")
    removed_ids = data.get("removed_ids")
    if not isinstance(campaigns, list) or not isinstance(removed_ids, list):
        raise ValueError
    ids: set[str] = set()
    for raw_campaign in campaigns:
        campaign = _require_dict(raw_campaign)
        campaign_id = _require_nonempty_string(campaign.get("id"))
        if campaign_id in ids:
            raise ValueError
        ids.add(campaign_id)
    if not all(isinstance(item, str) for item in removed_ids):
        raise ValueError


def _validate_schedules(payload: Any) -> None:
    data = _require_dict(payload)
    timezone_name = _require_nonempty_string(data.get("timezone"))
    ZoneInfo(timezone_name)
    date.fromisoformat(_require_nonempty_string(data.get("planner_anchor")))
    send_window = _require_dict(data.get("send_window"))
    start_hour = send_window.get("start_hour")
    end_hour = send_window.get("end_hour")
    if (
        type(start_hour) is not int
        or type(end_hour) is not int
        or not 0 <= start_hour <= 23
        or not 0 <= end_hour <= 23
        or not isinstance(send_window.get("enabled"), bool)
    ):
        raise ValueError
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError
    ids: set[str] = set()
    for raw_job in jobs:
        job = _require_dict(raw_job)
        job_id = _require_nonempty_string(job.get("id"))
        if job_id in ids:
            raise ValueError
        ids.add(job_id)
        fire_at = job.get("fire_at")
        if fire_at is not None:
            hour, separator, minute = _require_nonempty_string(fire_at).partition(":")
            if separator != ":" or not hour.isdecimal() or not minute.isdecimal():
                raise ValueError
            if not 0 <= int(hour) <= 23 or not 0 <= int(minute) <= 59:
                raise ValueError


def _validate_data_pairs(payloads: dict[str, Any]) -> None:
    if payloads["locations.json"]["locations"] != payloads["locations.sidecar.json"]["locations"]:
        raise BotImportError("inconsistent_data_pair:locations")
    roles = payloads["roles.json"]
    roles_sidecar = payloads["roles.sidecar.json"]
    if (
        roles["admin_user_ids"] != roles_sidecar["admin_user_ids"]
        or roles["role_definitions"] != roles_sidecar["role_definitions"]
    ):
        raise BotImportError("inconsistent_data_pair:roles")
    if payloads["dispatcher_users.json"] != payloads["dispatcher_users.sidecar.json"]["users"]:
        raise BotImportError("inconsistent_data_pair:dispatcher_users")


def _validate_members(members: list[zipfile.ZipInfo]) -> None:
    if len(members) > _MAX_ARCHIVE_MEMBERS:
        raise BotImportError("too_many_archive_members")
    seen: set[str] = set()
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or name.startswith("/")
            or "//" in name
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise BotImportError("unsafe_archive_path")
        normalized_name = name.casefold()
        if normalized_name in seen:
            raise BotImportError("duplicate_archive_member")
        seen.add(normalized_name)
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise BotImportError("archive_symlink")
        file_type = stat.S_IFMT(mode)
        if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise BotImportError("unsupported_archive_member")
        if member.flag_bits & 0x1:
            raise BotImportError("encrypted_archive_member")


def _parse_manifest(raw: bytes) -> dict[str, object]:
    try:
        manifest = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BotImportError("malformed_manifest") from exc
    if not isinstance(manifest, dict):
        raise BotImportError("malformed_manifest")
    return manifest


def _read_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    limit: int,
) -> bytes:
    with archive.open(member, "r") as source:
        raw = source.read(limit + 1)
        if len(raw) > limit or source.read(1):
            raise BotImportError("data_file_too_large")
        return raw


def _open_destination(path: Path) -> _DestinationHandle:
    parts = path.parts
    if not parts or path == Path(".") or ".." in parts:
        raise BotImportError("unsafe_destination_path")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if path.is_absolute():
        current_fd = os.open(path.anchor, flags | no_follow)
        components = parts[1:]
    else:
        current_fd = os.open(".", flags | no_follow)
        components = parts
    if not components:
        os.close(current_fd)
        raise BotImportError("unsafe_destination_path")

    directory_fd: int | None = None
    try:
        for component in components[:-1]:
            next_fd = _open_directory_component(current_fd, component, flags, no_follow)
            os.close(current_fd)
            current_fd = next_fd
        leaf_name = components[-1]
        created = False
        try:
            _reject_symlink_at(current_fd, leaf_name)
            directory_fd = os.open(leaf_name, flags | no_follow, dir_fd=current_fd)
        except FileNotFoundError:
            try:
                os.mkdir(leaf_name, mode=0o700, dir_fd=current_fd)
                created = True
                directory_fd = os.open(leaf_name, flags | no_follow, dir_fd=current_fd)
            except OSError as exc:
                raise BotImportError("destination_create_failed") from exc
        except BotImportError:
            raise
        except OSError as exc:
            raise BotImportError("destination_not_empty") from exc
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        if os.listdir(directory_fd):
            raise BotImportError("destination_not_empty")
        handle = _DestinationHandle(
            directory_fd=directory_fd,
            parent_fd=current_fd,
            leaf_name=leaf_name,
            created=created,
        )
        directory_fd = None
        return handle
    except Exception:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(current_fd)
        raise


def _open_directory_component(
    parent_fd: int,
    component: str,
    flags: int,
    no_follow: int,
) -> int:
    _reject_symlink_at(parent_fd, component)
    try:
        return os.open(component, flags | no_follow, dir_fd=parent_fd)
    except FileNotFoundError as exc:
        raise BotImportError("destination_parent_missing") from exc
    except OSError as exc:
        raise BotImportError("destination_parent_invalid") from exc


def _reject_symlink_at(parent_fd: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise BotImportError("destination_symlink")


def _create_temporary_file(directory_fd: int, filename: str) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(10):
        name = f".{filename}.{secrets.token_hex(8)}.importing"
        try:
            return name, os.open(name, flags, 0o600, dir_fd=directory_fd)
        except FileExistsError:
            continue
    raise BotImportError("temporary_name_exhausted")


def _verify_destination_unchanged(
    handle: _DestinationHandle,
    expected_files: set[str],
) -> None:
    try:
        current = os.stat(
            handle.leaf_name,
            dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError as exc:
        raise BotImportError("destination_changed") from exc
    opened = os.fstat(handle.directory_fd)
    if stat.S_ISLNK(current.st_mode) or (current.st_dev, current.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        raise BotImportError("destination_changed")
    if set(os.listdir(handle.directory_fd)) != expected_files:
        raise BotImportError("destination_changed")


def _rollback_import(
    created_files: list[str],
    temporary_files: list[str],
    handle: _DestinationHandle,
) -> None:
    for name in (*temporary_files, *created_files):
        with suppress(FileNotFoundError):
            os.unlink(name, dir_fd=handle.directory_fd)
    with suppress(OSError):
        os.fsync(handle.directory_fd)
    if handle.created:
        with suppress(OSError):
            os.rmdir(handle.leaf_name, dir_fd=handle.parent_fd)
            os.fsync(handle.parent_fd)
