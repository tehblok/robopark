"""Atomically publish verified release units without executing payload scripts."""

import os
import sys
import tempfile
from pathlib import Path

UNITS = (
    "robopark-commands.service",
    "robopark-commands.path",
    "robopark-bot.service",
    "robopark-bot.path",
    "robopark.service",
    "robopark-tuna.service",
    "robopark-updater.service",
    "robopark-doctor.service",
    "robopark-doctor.timer",
    "robopark-backup.service",
    "robopark-backup.timer",
    "robopark-watchdog.service",
    "robopark-watchdog.timer",
)
TMPFILES_SOURCE = "deploy/tmpfiles.d/robopark.conf"
TMPFILES_TARGET = "etc/tmpfiles.d/robopark.conf"


def _atomic_install(target_dir, name, content):
    target_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + name + ".", dir=target_dir)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target_dir / name)
        directory = os.open(target_dir, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_units(root):
    root = Path(root)
    release = (root / "opt/robopark/current").resolve(strict=True)
    if not release.is_relative_to((root / "opt/robopark/releases").resolve()):
        raise ValueError("invalid_release")
    target_dir = root / "etc/systemd/system"
    if target_dir.is_symlink() or target_dir.parent.is_symlink():
        raise ValueError("invalid_unit_directory")
    # Validate every source before replacing the first installed unit.
    contents = {}
    for name in UNITS:
        source = release / "deploy/systemd" / name
        if source.is_symlink() or not source.is_file():
            raise ValueError("missing_unit")
        contents[name] = source.read_bytes()
    terminal_names = (
        "robopark-terminal-setup.service", "robopark-terminal-broker.service",
        "robopark-terminal-maintenance@.service", "robopark-terminal-root@.service",
    )
    terminal = {}
    for name in terminal_names:
        source = release / "deploy/systemd" / name
        if source.is_symlink():
            raise ValueError("terminal_payload_invalid")
        if source.exists():
            if not source.is_file():
                raise ValueError("terminal_payload_invalid")
            terminal[name] = source.read_bytes()
    if terminal and len(terminal) != len(terminal_names):
        raise ValueError("terminal_payload_invalid")
    contents.update(terminal)
    ai_names = (
        "robopark-ai-setup.service", "robopark-ai.service", "robopark-ai-broker.service",
    )
    ai = {}
    for name in ai_names:
        source = release / "deploy/systemd" / name
        if source.is_symlink():
            raise ValueError("ai_payload_invalid")
        if source.exists():
            if not source.is_file():
                raise ValueError("ai_payload_invalid")
            ai[name] = source.read_bytes()
    if ai and len(ai) != len(ai_names):
        raise ValueError("ai_payload_invalid")
    contents.update(ai)
    tmpfiles_source = release / TMPFILES_SOURCE
    tmpfiles_target = root / TMPFILES_TARGET
    if tmpfiles_source.is_symlink() or not tmpfiles_source.is_file():
        raise ValueError("missing_tmpfiles")
    if (
        tmpfiles_target.parent.is_symlink()
        or tmpfiles_target.parent.parent.is_symlink()
    ):
        raise ValueError("invalid_tmpfiles_directory")
    tmpfiles_content = tmpfiles_source.read_bytes()
    for name, content in contents.items():
        _atomic_install(target_dir, name, content)
    _atomic_install(tmpfiles_target.parent, tmpfiles_target.name, tmpfiles_content)


if __name__ == "__main__":
    try:
        install_units(sys.argv[1])
    except (ValueError, OSError):
        print("Service unit installation failed", file=sys.stderr)
        sys.exit(1)
