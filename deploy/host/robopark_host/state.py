"""Durable, private host state primitives."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator


def atomic_write_json(path: Path, payload: Dict, mode: int = 0o600) -> None:
    """Atomically replace *path* with fsynced JSON protected by *mode*."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, allow_nan=False, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=".{0}.".format(target.name)
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
def host_operation(paths):
    """Standalone ownership; internal helpers run under their caller's host.lock.

    A durable command claim closes the gap while a consumer hands host.lock to
    its worker. Maintenance keeps interrupted transactions exclusive after reboot.
    """
    try:
        with exclusive_lock(paths.ops / "host.lock", blocking=False):
            if operation_pending(paths):
                raise HostBusy("host_busy")
            yield
    except BlockingIOError as exc:
        raise HostBusy("host_busy") from exc
