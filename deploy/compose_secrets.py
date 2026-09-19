#!/usr/bin/env python3
"""Create the root-owned credentials required by the direct Compose path."""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import tempfile
from pathlib import Path


def _atomic_private(path: Path, value: str, *, uid: int, gid: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fchmod(stream.fileno(), 0o600)
            os.fchown(stream.fileno(), uid, gid)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_private(path: Path, *, uid: int) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != uid
            or info.st_size > 4096
        ):
            raise ValueError("unsafe_compose_secret")
        value = stream.read(4097)
    if not value or len(value) > 4096:
        raise ValueError("unsafe_compose_secret")
    return value.rstrip("\n")


def bootstrap_compose_secrets(directory: Path, *, api_uid: int = 10001) -> Path:
    """Create or validate external secrets and return a non-secret env file."""
    directory = Path(directory)
    if os.geteuid() != 0 or not directory.is_absolute() or directory.is_symlink():
        raise PermissionError("root_private_directory_required")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    os.chown(directory, 0, 0)

    password_path = directory / "postgres-password"
    pgpass_path = directory / "pgpass"
    if password_path.exists() or password_path.is_symlink():
        password = _read_private(password_path, uid=0)
    else:
        password = secrets.token_urlsafe(48)
        _atomic_private(password_path, password + "\n", uid=0, gid=0)
    expected_pgpass = f"db:5432:robopark:robopark:{password}"
    if pgpass_path.exists() or pgpass_path.is_symlink():
        if _read_private(pgpass_path, uid=api_uid) != expected_pgpass:
            raise ValueError("compose_secret_mismatch")
    else:
        _atomic_private(pgpass_path, expected_pgpass + "\n", uid=api_uid, gid=api_uid)

    env_path = directory / "compose-secrets.env"
    _atomic_private(
        env_path,
        "ROBOPARK_POSTGRES_PASSWORD_FILE=" + str(password_path) + "\n"
        "ROBOPARK_PGPASS_FILE=" + str(pgpass_path) + "\n",
        uid=0,
        gid=0,
    )
    return env_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("/etc/robopark"))
    parser.add_argument("--api-uid", type=int, default=10001)
    arguments = parser.parse_args()
    print(bootstrap_compose_secrets(arguments.directory, api_uid=arguments.api_uid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
