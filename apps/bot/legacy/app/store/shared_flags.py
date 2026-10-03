"""Small runtime flags shared with Robopark's owner settings API."""

from __future__ import annotations

import fcntl
import os
import secrets
import stat
from pathlib import Path


def write_optional_flag(path: Path, value: bytes | None) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = os.open(
        path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    temporary = f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        try:
            existing = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
            raise OSError("unsafe_bot_flag")
        if value is None:
            if existing is not None:
                os.unlink(path.name, dir_fd=directory)
                os.fsync(directory)
            return
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        try:
            os.write(descriptor, value)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        fcntl.flock(directory, fcntl.LOCK_UN)
        os.close(directory)
