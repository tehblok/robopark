from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from sqlalchemy.orm import Session

from robopark_api.crypto import is_encrypted
from robopark_api.services import platform_settings


class TelegramBotDisabled(RuntimeError):
    pass


class TelegramBotTokenNotConfigured(RuntimeError):
    pass


class BotControlUnavailable(RuntimeError):
    pass


class BotControlStateInvalid(RuntimeError):
    pass


_CONTROL_DIRECTORY = "telegram-bot"
_CONTROL_FILENAME = "enabled.json"
_OPERATION_LOCK_FILENAME = ".operations.lock"
_MAX_CONTROL_BYTES = 128
_HEARTBEAT_FILENAME = "runtime-heartbeat"
_HEARTBEAT_MAX_AGE_SECONDS = 20
_START_GRACE_SECONDS = 90


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BotControlStateInvalid
        result[key] = value
    return result


def _open_directory_path(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    parts = path.parts
    if not path.is_absolute() or not parts:
        raise BotControlUnavailable
    current_fd: int | None = None
    try:
        current_fd = os.open(path.anchor, flags | no_follow)
        for component in parts[1:]:
            next_fd = os.open(component, flags | no_follow, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except OSError as exc:
        if current_fd is not None:
            os.close(current_fd)
        raise BotControlUnavailable from exc


def _open_control_directory(host_data_path: str, *, create: bool) -> int:
    root_fd = _open_directory_path(Path(host_data_path))
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        if create:
            with suppress(FileExistsError):
                os.mkdir(_CONTROL_DIRECTORY, mode=0o700, dir_fd=root_fd)
        return os.open(_CONTROL_DIRECTORY, flags | no_follow, dir_fd=root_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise BotControlUnavailable from exc
    finally:
        os.close(root_fd)


def desired_enabled(host_data_path: str) -> bool:
    try:
        directory_fd = _open_control_directory(host_data_path, create=False)
    except FileNotFoundError:
        return False
    except BotControlUnavailable as exc:
        if not Path(host_data_path).exists():
            return False
        raise exc
    try:
        try:
            descriptor = os.open(
                _CONTROL_FILENAME,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise BotControlStateInvalid from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
                raise BotControlStateInvalid
            raw = os.read(descriptor, _MAX_CONTROL_BYTES + 1)
            if len(raw) > _MAX_CONTROL_BYTES or os.read(descriptor, 1):
                raise BotControlStateInvalid
        finally:
            os.close(descriptor)
    finally:
        os.close(directory_fd)
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BotControlStateInvalid from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "enabled"}
        or payload.get("schema") != 1
        or not isinstance(payload.get("enabled"), bool)
    ):
        raise BotControlStateInvalid
    return payload["enabled"]


@contextmanager
def operation_lock(host_data_path: str) -> Iterator[None]:
    directory_fd = _open_control_directory(host_data_path, create=True)
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                _OPERATION_LOCK_FILENAME,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise BotControlUnavailable
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except BotControlUnavailable:
            raise
        except OSError as exc:
            raise BotControlUnavailable from exc
        yield
    finally:
        if descriptor is not None:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        os.close(directory_fd)


def set_desired_enabled(host_data_path: str, enabled: bool) -> None:
    with operation_lock(host_data_path):
        _set_desired_enabled_unlocked(host_data_path, enabled)


def _set_desired_enabled_unlocked(host_data_path: str, enabled: bool) -> None:
    directory_fd = _open_control_directory(host_data_path, create=True)
    temporary_name = f".enabled.{secrets.token_hex(12)}.tmp"
    descriptor: int | None = None
    try:
        try:
            current = os.stat(
                _CONTROL_FILENAME,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
                raise BotControlUnavailable
        except FileNotFoundError:
            pass
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        raw = (
            json.dumps({"schema": 1, "enabled": enabled}, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short control-state write")
            remaining = remaining[written:]
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            _CONTROL_FILENAME,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except BotControlUnavailable:
        raise
    except OSError as exc:
        raise BotControlUnavailable from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=directory_fd)
        os.close(directory_fd)


def status(db: Session, host_data_path: str) -> dict[str, object]:
    row = platform_settings.get_setting(db, platform_settings.TELEGRAM_BOT_TOKEN_KEY)
    token = platform_settings.get_telegram_bot_token(db)
    enabled = desired_enabled(host_data_path)
    return {
        "desired_enabled": enabled,
        "runtime_state": runtime_state(host_data_path, enabled=enabled),
        "token_configured": bool(token),
        "token_masked": platform_settings.mask_secret(token),
        "token_updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        "token_encrypted": bool(row and row.value and is_encrypted(row.value)),
    }


def runtime_state(host_data_path: str, *, enabled: bool) -> str:
    if not enabled:
        return "stopped"
    try:
        directory_fd = _open_control_directory(host_data_path, create=False)
    except (FileNotFoundError, BotControlUnavailable):
        return "unavailable"
    try:
        now = time.time()
        try:
            descriptor = os.open(
                _HEARTBEAT_FILENAME,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except (FileNotFoundError, OSError):
            descriptor = None
        if descriptor is not None:
            try:
                metadata = os.fstat(descriptor)
                if (
                    stat.S_ISREG(metadata.st_mode)
                    and stat.S_IMODE(metadata.st_mode) == 0o600
                    and metadata.st_size == 2
                    and os.read(descriptor, 3) == b"1\n"
                    and -5 <= now - metadata.st_mtime <= _HEARTBEAT_MAX_AGE_SECONDS
                ):
                    return "running"
            finally:
                os.close(descriptor)
        try:
            control = os.stat(_CONTROL_FILENAME, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            return "unavailable"
        if stat.S_ISREG(control.st_mode) and 0 <= now - control.st_mtime <= _START_GRACE_SECONDS:
            return "starting"
        return "unavailable"
    finally:
        os.close(directory_fd)


def set_token(db: Session, token: str) -> None:
    platform_settings.set_telegram_bot_token(db, token)


def runtime_token(db: Session, host_data_path: str) -> str:
    if not desired_enabled(host_data_path):
        raise TelegramBotDisabled
    token = platform_settings.get_telegram_bot_token(db)
    if not token:
        raise TelegramBotTokenNotConfigured
    return token
