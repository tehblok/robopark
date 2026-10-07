from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from robopark_api.services import bot_settings


class BotConfigError(ValueError):
    pass


class UnknownSection(BotConfigError):
    pass


class UnsafeState(BotConfigError):
    pass


class InvalidSchema(BotConfigError):
    pass


class RevisionConflict(BotConfigError):
    pass


class BotMustBeDisabled(BotConfigError):
    pass


@dataclass(frozen=True)
class Section:
    revision: str
    value: Any


_JSON_FILES = {
    "roles": "roles.json",
    "users": "dispatcher_users.json",
    "locations": "locations.json",
    "schedules": "schedules.json",
    "broadcasts": "broadcasts.json",
    "campaigns": "sk_campaigns.json",
}
_RUNTIME_FILES = {
    "profile": ".telegram_profile",
    "dispatcher_pause": ".dispatcher_paused",
    "send_pause": ".send_paused",
}
_AUXILIARY_SECTION = "auxiliary_tracker_queues"
_AUXILIARY_FILENAME = "robopark-settings.json"
SECTION_NAMES = (*_JSON_FILES, _AUXILIARY_SECTION, *_RUNTIME_FILES)
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_ITEMS = 5_000
_MAX_TEXT = 4_096
_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PERMISSIONS = {
    "global_search",
    "location_tags",
    "zip_wait",
    "moves",
    "history",
    "qr",
    "locations_edit",
    "users_roles",
    "profile_switch",
    "pause_monitor",
}
_DEFAULTS: dict[str, Any] = {
    "roles": {"version": 1, "admin_user_ids": [], "role_definitions": {}},
    "users": {},
    "locations": {"version": 1, "locations": [], "status_tags": []},
    "schedules": {
        "timezone": "Europe/Moscow",
        "planner_anchor": "2026-08-07",
        "send_window": {"start_hour": 9, "end_hour": 21, "enabled": True},
        "jobs": [],
    },
    "broadcasts": {"version": 1, "campaigns": [], "removed_ids": []},
    "campaigns": {"version": 1, "campaigns": []},
}


def _duplicate_rejector(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise InvalidSchema
        value[key] = item
    return value


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute() or not path.parts:
        raise UnsafeState
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise UnsafeState from exc


def _open_data_directory(host_data_path: str, *, create: bool) -> int | None:
    root = Path(host_data_path)
    if not root.exists():
        if not create:
            return None
        root.mkdir(mode=0o700, parents=True)
    root_fd = _open_absolute_directory(root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current = root_fd
    try:
        for name in ("telegram-bot", "data"):
            if create:
                with suppress(FileExistsError):
                    os.mkdir(name, mode=0o700, dir_fd=current)
            try:
                next_fd = os.open(name, flags, dir_fd=current)
            except FileNotFoundError:
                os.close(current)
                return None
            os.close(current)
            current = next_fd
        return current
    except OSError as exc:
        os.close(current)
        raise UnsafeState from exc


def _open_telegram_directory(host_data_path: str) -> int | None:
    root = Path(host_data_path)
    if not root.exists():
        return None
    root_fd = _open_absolute_directory(root)
    try:
        descriptor = os.open(
            "telegram-bot",
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
    except FileNotFoundError:
        os.close(root_fd)
        return None
    except OSError as exc:
        os.close(root_fd)
        raise UnsafeState from exc
    os.close(root_fd)
    return descriptor


def _read_file(directory_fd: int | None, filename: str) -> bytes | None:
    if directory_fd is None:
        return None
    try:
        descriptor = os.open(
            filename,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnsafeState from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or mode & 0o022
            or mode & 0o111
            or metadata.st_size > _MAX_JSON_BYTES
        ):
            raise UnsafeState
        raw = os.read(descriptor, _MAX_JSON_BYTES + 1)
        if len(raw) > _MAX_JSON_BYTES or os.read(descriptor, 1):
            raise UnsafeState
        return raw
    finally:
        os.close(descriptor)


def _parse_json(section: str, raw: bytes) -> Any:
    try:
        value = json.loads(raw, object_pairs_hook=_duplicate_rejector)
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidSchema) as exc:
        raise InvalidSchema from exc
    _validate(section, value)
    return value


def _known(data: dict[str, Any], allowed: set[str]) -> None:
    if set(data) - allowed:
        raise InvalidSchema


def _text(value: Any, *, required: bool = True, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise InvalidSchema
    return value


def _validate_roles(value: Any) -> None:
    if not isinstance(value, dict):
        raise InvalidSchema
    _known(value, {"version", "admin_user_ids", "role_definitions", "updated_at"})
    if value.get("version", 1) != 1:
        raise InvalidSchema
    admin_ids = value.get("admin_user_ids")
    definitions = value.get("role_definitions")
    if not isinstance(admin_ids, list) or len(admin_ids) > 1_000:
        raise InvalidSchema
    if any(type(item) is not int or item <= 0 for item in admin_ids) or len(set(admin_ids)) != len(
        admin_ids
    ):
        raise InvalidSchema
    if not isinstance(definitions, dict) or len(definitions) > 100:
        raise InvalidSchema
    for name, raw in definitions.items():
        if not isinstance(name, str) or not _ROLE_NAME.fullmatch(name) or not isinstance(raw, dict):
            raise InvalidSchema
        _known(raw, {"label", "permissions"})
        _text(raw.get("label"), limit=120)
        permissions = raw.get("permissions")
        if (
            not isinstance(permissions, list)
            or len(permissions) > len(_PERMISSIONS)
            or any(not isinstance(item, str) for item in permissions)
            or any(item not in _PERMISSIONS for item in permissions)
            or len(set(permissions)) != len(permissions)
        ):
            raise InvalidSchema
    if "updated_at" in value:
        _text(value["updated_at"], limit=64)


def _validate_users(value: Any) -> None:
    if not isinstance(value, dict) or len(value) > _MAX_ITEMS:
        raise InvalidSchema
    allowed = {"name", "username", "role", "access", "allowed_tags", "location", "greeting"}
    for user_id, profile in value.items():
        if not isinstance(user_id, str) or not user_id.isdecimal() or int(user_id) <= 0:
            raise InvalidSchema
        if not isinstance(profile, dict):
            raise InvalidSchema
        _known(profile, allowed)
        role = profile.get("role")
        if not isinstance(role, str) or not _ROLE_NAME.fullmatch(role):
            raise InvalidSchema
        if profile.get("access") not in {"location", "global"}:
            raise InvalidSchema
        tags = profile.get("allowed_tags")
        if tags != "*" and not (
            isinstance(tags, list)
            and len(tags) <= 200
            and all(isinstance(tag, str) and 0 < len(tag.strip()) <= 120 for tag in tags)
        ):
            raise InvalidSchema
        for field in ("name", "username", "location", "greeting"):
            if field in profile and profile[field] is not None:
                _text(
                    profile[field], required=False, limit=_MAX_TEXT if field == "greeting" else 160
                )


def _validate_chat(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise InvalidSchema
    _known(value, {"chat_id", "thread_id"})
    if type(value.get("chat_id")) is not int:
        raise InvalidSchema
    if value.get("thread_id") is not None and type(value["thread_id"]) is not int:
        raise InvalidSchema


def _validate_locations(value: Any) -> None:
    if not isinstance(value, dict):
        raise InvalidSchema
    _known(value, {"version", "updated_at", "locations", "status_tags", "metadata"})
    if value.get("version", 1) not in {1, 2}:
        raise InvalidSchema
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise InvalidSchema
    _known(metadata, {"deleted_location_keys", "deleted_location_slugs"})
    for field in ("deleted_location_keys", "deleted_location_slugs"):
        items = metadata.get(field, [])
        if (
            not isinstance(items, list)
            or len(items) > 500
            or any(not isinstance(item, str) or not item.strip() for item in items)
            or len(set(items)) != len(items)
        ):
            raise InvalidSchema
    rows = value.get("locations")
    if not isinstance(rows, list) or len(rows) > 500:
        raise InvalidSchema
    keys: set[str] = set()
    participation_keys = {
        "hourly_png",
        "killswitch",
        "dispatcher_anchor",
        "sdcwh_zip",
        "robomaint_moves",
    }
    for row in rows:
        if not isinstance(row, dict):
            raise InvalidSchema
        _known(
            row,
            {
                "key",
                "display_name",
                "tracker_tag",
                "slug",
                "chats",
                "chat_overrides",
                "logistics",
                "participation",
                "report_window",
            },
        )
        key = _text(row.get("key"), limit=120)
        if key in keys:
            raise InvalidSchema
        keys.add(key)
        for field in ("display_name", "tracker_tag", "slug"):
            _text(row.get(field), limit=120)
        chats = row.get("chats")
        if not isinstance(chats, dict):
            raise InvalidSchema
        _known(chats, {"prod", "test"})
        _validate_chat(chats.get("prod"))
        _validate_chat(chats.get("test"))
        overrides = row.get("chat_overrides", {})
        if (
            not isinstance(overrides, dict)
            or set(overrides) - {"prod", "test"}
            or any(not isinstance(flag, bool) for flag in overrides.values())
        ):
            raise InvalidSchema
        report_window = row.get("report_window")
        if report_window is not None:
            if not isinstance(report_window, dict):
                raise InvalidSchema
            _known(report_window, {"start_hour", "end_hour"})
            start, end = report_window.get("start_hour"), report_window.get("end_hour")
            if type(start) is not int or type(end) is not int or not 0 <= start <= end <= 23:
                raise InvalidSchema
        logistics = row.get("logistics")
        if logistics is not None:
            if not isinstance(logistics, dict):
                raise InvalidSchema
            _known(logistics, {"prod", "test"})
            _validate_chat(logistics.get("prod"))
            _validate_chat(logistics.get("test"))
        participation = row.get("participation")
        if not isinstance(participation, dict) or set(participation) - participation_keys:
            raise InvalidSchema
        if any(not isinstance(flag, bool) for flag in participation.values()):
            raise InvalidSchema
    tags = value.get("status_tags", [])
    if (
        not isinstance(tags, list)
        or len(tags) > 100
        or any(not isinstance(tag, str) for tag in tags)
    ):
        raise InvalidSchema


def _validate_time(value: Any) -> None:
    text = _text(value, limit=5)
    try:
        hour, minute = (int(part) for part in text.split(":"))
    except (ValueError, TypeError) as exc:
        raise InvalidSchema from exc
    if len(text) != 5 or text[2] != ":" or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise InvalidSchema


def _validate_schedules(value: Any) -> None:
    if not isinstance(value, dict):
        raise InvalidSchema
    _known(
        value, {"version", "deleted_job_ids", "timezone", "planner_anchor", "send_window", "jobs"}
    )
    if value.get("version", 1) not in {1, 2}:
        raise InvalidSchema
    deleted = value.get("deleted_job_ids", [])
    if (
        not isinstance(deleted, list)
        or len(deleted) > 1_000
        or any(not isinstance(item, str) or not item.strip() for item in deleted)
        or len(set(deleted)) != len(deleted)
    ):
        raise InvalidSchema
    try:
        ZoneInfo(_text(value.get("timezone"), limit=80))
        date.fromisoformat(_text(value.get("planner_anchor"), limit=10))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidSchema from exc
    window = value.get("send_window")
    if not isinstance(window, dict):
        raise InvalidSchema
    _known(window, {"start_hour", "end_hour", "enabled"})
    start, end = window.get("start_hour"), window.get("end_hour")
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start <= end <= 23
        or not isinstance(window.get("enabled"), bool)
    ):
        raise InvalidSchema
    jobs = value.get("jobs")
    if not isinstance(jobs, list) or len(jobs) > 1_000:
        raise InvalidSchema
    ids: set[str] = set()
    allowed = {
        "id",
        "label",
        "enabled",
        "fire_at",
        "weekdays",
        "kind",
        "alternate",
        "text",
        "locations",
        "link",
        "groups",
    }
    for job in jobs:
        if not isinstance(job, dict):
            raise InvalidSchema
        _known(job, allowed)
        job_id = _text(job.get("id"), limit=128)
        if job_id in ids:
            raise InvalidSchema
        ids.add(job_id)
        _text(job.get("label"), limit=200)
        if not isinstance(job.get("enabled"), bool):
            raise InvalidSchema
        _validate_time(job.get("fire_at"))
        if job.get("weekdays") is not None and not (
            isinstance(job["weekdays"], list)
            and len(job["weekdays"]) <= 7
            and all(type(day) is int and 0 <= day <= 6 for day in job["weekdays"])
        ):
            raise InvalidSchema
        for field in ("text", "link"):
            if field in job:
                _text(job[field], required=False, limit=_MAX_TEXT)
        if "locations" in job and not (
            isinstance(job["locations"], list)
            and len(job["locations"]) <= 500
            and all(isinstance(item, str) and item.strip() for item in job["locations"])
        ):
            raise InvalidSchema


def _validate_campaign_row(row: Any, *, broadcast: bool) -> str:
    if not isinstance(row, dict):
        raise InvalidSchema
    common = {"id", "enabled", "fire_at", "repeat", "created_at", "created_by"}
    allowed = common | (
        {"label", "text", "builtin", "location_mode", "location_keys"}
        if broadcast
        else {"tag", "query", "location_keys"}
    )
    _known(row, allowed)
    campaign_id = _text(row.get("id"), limit=128)
    if not isinstance(row.get("enabled"), bool):
        raise InvalidSchema
    _validate_time(row.get("fire_at"))
    if row.get("repeat") not in {"once", "daily", "weekdays"}:
        raise InvalidSchema
    if broadcast:
        _text(row.get("text"), limit=_MAX_TEXT)
        if "label" in row:
            _text(row["label"], limit=200)
        if row.get("location_mode") not in {"all", "hourly_png", "keys"}:
            raise InvalidSchema
    else:
        _text(row.get("tag"), limit=200)
        _text(row.get("query"), limit=500)
    if "location_keys" in row and not (
        isinstance(row["location_keys"], list)
        and len(row["location_keys"]) <= 500
        and all(isinstance(item, str) and item.strip() for item in row["location_keys"])
    ):
        raise InvalidSchema
    return campaign_id


def _validate_campaigns(value: Any, *, broadcast: bool) -> None:
    if not isinstance(value, dict):
        raise InvalidSchema
    allowed = {"version", "campaigns", "removed_ids"} if broadcast else {"version", "campaigns"}
    _known(value, allowed)
    if value.get("version", 1) != 1:
        raise InvalidSchema
    rows = value.get("campaigns")
    if not isinstance(rows, list) or len(rows) > 2_000:
        raise InvalidSchema
    ids = [_validate_campaign_row(row, broadcast=broadcast) for row in rows]
    if len(set(ids)) != len(ids):
        raise InvalidSchema
    if broadcast:
        removed = value.get("removed_ids")
        if (
            not isinstance(removed, list)
            or len(removed) > 2_000
            or any(not isinstance(item, str) for item in removed)
        ):
            raise InvalidSchema


def _validate(section: str, value: Any) -> None:
    if section == "roles":
        _validate_roles(value)
    elif section == "users":
        _validate_users(value)
    elif section == "locations":
        _validate_locations(value)
    elif section == "schedules":
        _validate_schedules(value)
    elif section == "broadcasts":
        _validate_campaigns(value, broadcast=True)
    elif section == "campaigns":
        _validate_campaigns(value, broadcast=False)
    elif section == _AUXILIARY_SECTION:
        if (
            not isinstance(value, list)
            or len(value) > 32
            or any(
                not isinstance(queue, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{1,31}", queue) is None
                for queue in value
            )
            or len(set(value)) != len(value)
        ):
            raise InvalidSchema
    elif section == "profile":
        if value not in {"prod", "test"}:
            raise InvalidSchema
    elif section in {"dispatcher_pause", "send_pause"}:
        if not isinstance(value, bool):
            raise InvalidSchema
    else:
        raise UnknownSection
    if section in _JSON_FILES and len(_canonical(value)) > _MAX_JSON_BYTES:
        raise InvalidSchema


def _read_json_section(directory_fd: int | None, section: str) -> Section:
    raw = _read_file(directory_fd, _JSON_FILES[section])
    if raw is None:
        value = _DEFAULTS[section]
        raw = _canonical(value)
    else:
        value = _parse_json(section, raw)
    return Section(revision=_revision(raw), value=value)


def _read_runtime_section(directory_fd: int | None, section: str) -> Section:
    raw = _read_file(directory_fd, _RUNTIME_FILES[section])
    if section == "profile":
        if raw is None:
            value = "prod"
        else:
            try:
                value = raw.decode().strip()
            except UnicodeDecodeError as exc:
                raise InvalidSchema from exc
            _validate(section, value)
    else:
        if raw is not None and raw != b"1\n":
            raise InvalidSchema
        value = raw is not None
    return Section(revision=_revision(_canonical(value)), value=value)


def _read_auxiliary_section(directory_fd: int | None) -> Section:
    raw = _read_file(directory_fd, _AUXILIARY_FILENAME)
    if raw is None:
        value: list[str] = []
        raw = _canonical({"version": 1, _AUXILIARY_SECTION: value})
    else:
        try:
            payload = json.loads(raw, object_pairs_hook=_duplicate_rejector)
        except (UnicodeDecodeError, json.JSONDecodeError, InvalidSchema) as exc:
            raise InvalidSchema from exc
        if not isinstance(payload, dict):
            raise InvalidSchema
        _known(payload, {"version", _AUXILIARY_SECTION})
        if payload.get("version") != 1:
            raise InvalidSchema
        value = payload.get(_AUXILIARY_SECTION)
        _validate(_AUXILIARY_SECTION, value)
    return Section(revision=_revision(raw), value=value)


def read_all(host_data_path: str) -> dict[str, Section]:
    directory_fd = _open_data_directory(host_data_path, create=False)
    try:
        result: dict[str, Section] = {}
        for name in _JSON_FILES:
            try:
                result[name] = _read_json_section(directory_fd, name)
            except BotConfigError as exc:
                raise type(exc)(name) from exc
        try:
            result[_AUXILIARY_SECTION] = _read_auxiliary_section(directory_fd)
        except BotConfigError as exc:
            raise type(exc)(_AUXILIARY_SECTION) from exc
        for name in _RUNTIME_FILES:
            try:
                result[name] = _read_runtime_section(directory_fd, name)
            except BotConfigError as exc:
                raise type(exc)(name) from exc
        return result
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def read_legacy_sources(host_data_path: str) -> list[tuple[str, dict[str, Section]]]:
    """Read legacy JSON from both historically used directories without merging them."""
    sources: list[tuple[str, dict[str, Section]]] = []
    for label, directory_fd in (
        ("flat", _open_telegram_directory(host_data_path)),
        ("nested", _open_data_directory(host_data_path, create=False)),
    ):
        if directory_fd is None:
            continue
        try:
            raw_by_section = {
                section: _read_file(directory_fd, filename)
                for section, filename in _JSON_FILES.items()
            }
            if not any(raw_by_section[name] is not None for name in _JSON_FILES):
                continue
            sections: dict[str, Section] = {}
            for section, raw in raw_by_section.items():
                if raw is None:
                    value = _DEFAULTS[section]
                    raw = _canonical(value)
                else:
                    value = _parse_json(section, raw)
                sections[section] = Section(revision=_revision(raw), value=value)
            sources.append((label, sections))
        except BotConfigError as exc:
            raise type(exc)(f"{label}:{exc}") from exc
        finally:
            os.close(directory_fd)
    return sources


def _write_atomic(directory_fd: int, filename: str, raw: bytes) -> None:
    temporary = f".{filename}.{secrets.token_hex(8)}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        output_descriptor = descriptor
        descriptor = None
        with os.fdopen(output_descriptor, "wb", closefd=True) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    except OSError as exc:
        raise UnsafeState from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory_fd)


def _sidecar(section: str, value: Any) -> tuple[str, bytes] | None:
    if section == "roles":
        return "roles.sidecar.json", _canonical(value)
    if section == "users":
        return "dispatcher_users.sidecar.json", _canonical({"version": 1, "users": value})
    if section == "locations":
        return "locations.sidecar.json", _canonical({"locations": value["locations"]})
    return None


def _write_runtime(directory_fd: int, section: str, value: Any) -> None:
    filename = _RUNTIME_FILES[section]
    should_exist = value == "test" if section == "profile" else value is True
    if should_exist:
        raw = b"test\n" if section == "profile" else b"1\n"
        _write_atomic(directory_fd, filename, raw)
        return
    try:
        metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise UnsafeState
        os.unlink(filename, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise UnsafeState from exc


def update(host_data_path: str, section: str, value: Any, expected_revision: str) -> Section:
    if section not in SECTION_NAMES:
        raise UnknownSection
    _validate(section, value)
    if section in {*_JSON_FILES, _AUXILIARY_SECTION} and bot_settings.desired_enabled(
        host_data_path
    ):
        raise BotMustBeDisabled
    directory_fd = _open_data_directory(host_data_path, create=True)
    if directory_fd is None:
        raise UnsafeState
    try:
        fcntl.flock(directory_fd, fcntl.LOCK_EX)
        current = (
            _read_json_section(directory_fd, section)
            if section in _JSON_FILES
            else _read_auxiliary_section(directory_fd)
            if section == _AUXILIARY_SECTION
            else _read_runtime_section(directory_fd, section)
        )
        normalized_revision = expected_revision.strip().removeprefix("W/").strip('"')
        if not secrets.compare_digest(current.revision, normalized_revision):
            raise RevisionConflict
        if section in _JSON_FILES:
            raw = _canonical(value)
            sidecar = _sidecar(section, value)
            if sidecar is not None:
                _write_atomic(directory_fd, *sidecar)
            _write_atomic(directory_fd, _JSON_FILES[section], raw)
            return Section(revision=_revision(raw), value=value)
        if section == _AUXILIARY_SECTION:
            raw = _canonical({"version": 1, _AUXILIARY_SECTION: value})
            _write_atomic(directory_fd, _AUXILIARY_FILENAME, raw)
            return Section(revision=_revision(raw), value=value)
        _write_runtime(directory_fd, section, value)
        return _read_runtime_section(directory_fd, section)
    finally:
        os.close(directory_fd)


def auxiliary_tracker_queues(host_data_path: str) -> tuple[str, ...]:
    directory_fd = _open_data_directory(host_data_path, create=False)
    try:
        return tuple(_read_auxiliary_section(directory_fd).value)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
