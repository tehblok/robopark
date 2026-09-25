from __future__ import annotations

import getpass
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RoyalCredentials:
    username: str
    password: str = field(repr=False)
    role: str = "royal"


def _validate(credentials: RoyalCredentials) -> None:
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", credentials.username) is None:
        raise ValueError("invalid_royal_username")
    password = credentials.password
    classes = (
        any(character.islower() for character in password),
        any(character.isupper() for character in password),
        any(character.isdigit() for character in password),
        any(not character.isalnum() for character in password),
    )
    if len(password) < 12 or sum(classes) < 3 or credentials.username.lower() in password.lower():
        raise ValueError("royal_password_policy")


def collect_royal_credentials(
    *,
    input_fn: Callable[[str], str] = input,
    getpass_fn: Callable[[str], str] = getpass.getpass,
) -> RoyalCredentials:
    username = input_fn("Логин первого royal [royal]: ").strip() or "royal"
    password = getpass_fn("Пароль первого royal: ")
    repeated = getpass_fn("Повторите пароль: ")
    if password != repeated:
        raise ValueError("royal_password_mismatch")
    credentials = RoyalCredentials(username=username, password=password)
    _validate(credentials)
    return credentials


@contextmanager
def credential_file(credentials: RoyalCredentials, directory: Path) -> Iterator[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise ValueError("unsafe_credential_directory")
    os.chmod(directory, 0o700)
    descriptor, name = tempfile.mkstemp(prefix="seed-", suffix=".json", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(
                {
                    "username": credentials.username,
                    "password": credentials.password,
                    "role": credentials.role,
                },
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            stream.flush()
            os.fsync(stream.fileno())
        yield path
    finally:
        path.unlink(missing_ok=True)


def seed_command(path: Path, *, compose_prefix: list[str] | None = None) -> list[str]:
    path = Path(path).resolve(strict=True)
    prefix = compose_prefix or ["docker", "compose", "--project-name", "robopark"]
    return [
        *prefix,
        "run",
        "--rm",
        "--no-deps",
        "--user",
        "0:0",
        "-v",
        f"{path}:/run/robopark/seed.json:ro",
        "api",
        "python",
        "-m",
        "robopark_api.seed_from_file",
    ]
