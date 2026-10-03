"""Durable, private host state primitives."""

import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

_PROGRESS_PHASES = {
    "accepted": 0,
    "executing": 1,
    "succeeded": 2,
    "failed": 2,
    "manual_recovery_required": 2,
}


def atomic_write_json(path: Path, payload: dict, mode: int = 0o600) -> None:
    """Atomically replace *path* with fsynced JSON protected by *mode*."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, allow_nan=False, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}."
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(target))
        directory_descriptor = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_operation_progress(paths, operation_id: str) -> dict | None:
    """Read one private, bounded operation receipt without accepting aliases."""

    identity = str(UUID(operation_id))
    if identity != operation_id:
        raise ValueError("invalid_operation_id")
    target = paths.state / "operation-progress" / f"{identity}.json"
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("invalid_operation_progress") from exc
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > 4096
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ValueError("invalid_operation_progress")
            value = json.loads(stream.read(4097))
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid_operation_progress") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "operation_id", "phase", "progress"}
        or value.get("schema") != 1
        or value.get("operation_id") != identity
        or value.get("phase") not in _PROGRESS_PHASES
        or type(value.get("progress")) is not int
        or not 0 <= value["progress"] <= 100
    ):
        raise ValueError("invalid_operation_progress")
    return value


def write_operation_progress(
    paths,
    operation_id: str,
    phase: str,
    progress: int,
    *,
    check_space: bool = True,
) -> dict:
    """Atomically publish monotonic private progress suitable for crash resume."""

    from .storage_compatibility import require_storage_operations

    require_storage_operations(paths, check_space=check_space)

    if phase not in _PROGRESS_PHASES or type(progress) is not int or not 0 <= progress <= 100:
        raise ValueError("invalid_operation_progress")
    previous = read_operation_progress(paths, operation_id)
    if previous and (
        _PROGRESS_PHASES[phase] < _PROGRESS_PHASES[previous["phase"]]
        or progress < previous["progress"]
        or previous["phase"] in {"succeeded", "failed", "manual_recovery_required"}
        and phase != previous["phase"]
    ):
        raise ValueError("progress_regression")
    value = {
        "schema": 1,
        "operation_id": operation_id,
        "phase": phase,
        "progress": progress,
    }
    atomic_write_json(paths.state / "operation-progress" / f"{operation_id}.json", value)
    return value


@contextmanager
def exclusive_lock(path: Path, *, blocking: bool = True) -> Generator[None, None, None]:
    """Hold an advisory exclusive lock for the duration of the context."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(target), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class HostBusy(Exception):
    """Another owner or an unfinished transaction forbids standalone mutations."""


def operation_pending(paths):
    return any(
        path.exists() or path.is_symlink()
        for path in (
            paths.state / "maintenance.json",
            paths.ops / "public/maintenance.json",
            paths.state / "command-request.json",
            paths.ops / "inbox/approved.json",
        )
    )


@contextmanager
def host_operation(paths, *, check_space: bool = True):
    """Standalone ownership; internal helpers run under their caller's host.lock.

    A durable command claim closes the gap while a consumer hands host.lock to
    its worker. Maintenance keeps interrupted transactions exclusive after reboot.
    """
    from .storage_compatibility import require_storage_operations

    require_storage_operations(paths, check_space=check_space)
    try:
        with exclusive_lock(paths.host_lock, blocking=False):
            if operation_pending(paths):
                raise HostBusy("host_busy")
            yield
    except BlockingIOError as exc:
        raise HostBusy("host_busy") from exc
