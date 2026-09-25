from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from robopark_api.config import Settings
from robopark_api.db import SessionLocal
from robopark_api.seed import ensure_seed_user

DEFAULT_SEED_FILE = Path("/run/robopark/seed.json")
MAX_SEED_BYTES = 16 * 1024


class SeedFileError(ValueError):
    pass


def read_seed_file(path: Path) -> tuple[str, str, str]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or not 0 < info.st_size <= MAX_SEED_BYTES
            ):
                raise SeedFileError("unsafe_seed_file")
            raw = source.read(MAX_SEED_BYTES + 1)
    except SeedFileError:
        raise
    except OSError as error:
        raise SeedFileError("unsafe_seed_file") from error
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SeedFileError("invalid_seed_file") from error
    if not isinstance(value, dict) or set(value) != {"username", "password", "role"}:
        raise SeedFileError("invalid_seed_file")
    username, password, role = value["username"], value["password"], value["role"]
    if (
        not isinstance(username, str)
        or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username) is None
        or not isinstance(password, str)
        or not 12 <= len(password) <= 1024
        or role != "royal"
    ):
        raise SeedFileError("invalid_seed_file")
    return username, password, role


def main() -> int:
    username, password, role = read_seed_file(DEFAULT_SEED_FILE)
    settings = Settings(seed_username=username, seed_password=password, seed_role=role)
    with SessionLocal() as database:
        ensure_seed_user(database, settings)
    del password, settings
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
