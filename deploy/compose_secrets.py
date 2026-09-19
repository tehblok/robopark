#!/usr/bin/env python3
"""Create the root-owned credentials required by the direct Compose path."""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import tempfile
from pathlib import Path

SNAPSHOT_CONFIG_KEYS = frozenset(
    {
        "ROBOPARK_ROLE",
        "ROBOPARK_DATABASE_PROFILE",
        "ROBOPARK_HOST_PROFILE",
        "CORS_ORIGINS",
        "SESSION_COOKIE_NAME",
        "SESSION_IDLE_SECONDS",
        "SESSION_ABSOLUTE_TTL_SECONDS",
        "COOKIE_SECURE",
        "COOKIE_SAMESITE",
        "PASSWORD_MIN_LENGTH",
        "PASSWORD_REQUIRE_COMPLEXITY",
        "LOGIN_MAX_ATTEMPTS",
        "LOGIN_ATTEMPT_WINDOW_SECONDS",
        "LOGIN_LOCKOUT_SECONDS",
        "REGISTER_MAX_ATTEMPTS",
        "SEED_USERNAME",
        "SEED_ROLE",
        "DEV_SEED",
        "UVICORN_WORKERS",
    }
)


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
            or info.st_nlink != 1
            or info.st_size > 4096
        ):
            raise ValueError("unsafe_compose_secret")
        value = stream.read(4097)
    if not value or len(value) > 4096:
        raise ValueError("unsafe_compose_secret")
    return value.rstrip("\n")


def _read_host_env(path: Path, *, owner_uid: int = 0) -> str:
    """Read a bounded root-private Compose config without following links."""
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != owner_uid
                or info.st_nlink != 1
                or not 0 < info.st_size <= 65536
            ):
                raise ValueError("unsafe_host_env")
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("unsafe_host_env")
        return raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError("unsafe_host_env") from error


def validate_host_env(path: Path, *, owner_uid: int = 0) -> None:
    """Reject a secret-bearing Compose config unless it is a private file."""
    _read_host_env(path, owner_uid=owner_uid)


def _snapshot_projection(path: Path, *, owner_uid: int = 0) -> str:
    values: dict[str, str] = {}
    for raw_line in _read_host_env(path, owner_uid=owner_uid).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not key
            or not key.replace("_", "A").isalnum()
            or key in values
        ):
            raise ValueError("unsafe_host_env")
        if key not in SNAPSHOT_CONFIG_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        if "\n" in value or "\r" in value:
            raise ValueError("unsafe_host_env")
        values[key] = value
    return "".join(f"{key}={value}\n" for key, value in values.items())


def bootstrap_compose_secrets(
    directory: Path,
    *,
    host_env: Path | None = None,
    host_env_owner_uid: int = 0,
    api_uid: int = 10001,
) -> Path:
    """Create or validate external secrets and return a non-secret env file."""
    directory = Path(directory)
    if os.geteuid() != 0 or not directory.is_absolute() or directory.is_symlink():
        raise PermissionError("root_private_directory_required")
    projection = (
        _snapshot_projection(host_env, owner_uid=host_env_owner_uid)
        if host_env is not None
        else None
    )
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

    snapshot_path = directory / "snapshot.env"
    if projection is not None:
        _atomic_private(snapshot_path, projection, uid=api_uid, gid=api_uid)

    env_path = directory / "compose-secrets.env"
    snapshot_line = (
        "ROBOPARK_SNAPSHOT_CONFIG_FILE=" + str(snapshot_path) + "\n"
        if host_env is not None
        else ""
    )
    _atomic_private(
        env_path,
        "ROBOPARK_POSTGRES_PASSWORD_FILE=" + str(password_path) + "\n"
        "ROBOPARK_PGPASS_FILE=" + str(pgpass_path) + "\n"
        + snapshot_line,
        uid=0,
        gid=0,
    )
    return env_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("/etc/robopark"))
    parser.add_argument("--api-uid", type=int, default=10001)
    parser.add_argument("--host-env", type=Path)
    arguments = parser.parse_args()
    print(
        bootstrap_compose_secrets(
            arguments.directory,
            host_env=arguments.host_env,
            api_uid=arguments.api_uid,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
