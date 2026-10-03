"""Optional terminal runtime activation; compatible with releases without PTYs."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from .release import ReleaseError, unique_object
from .state import atomic_write_json

TERMINAL_UNITS = (
    "robopark-terminal-setup.service",
    "robopark-terminal-broker.service",
    "robopark-terminal-maintenance@.service",
    "robopark-terminal-root@.service",
)
BROKER_UNIT = TERMINAL_UNITS[1]


def reconcile_terminal_compose(paths) -> bool:
    """Admit the API bridge omitted by pre-terminal OTA renderers before core starts.

    Boot recovery calls this after choosing the actual current release. Its caller
    owns startup ordering; taking host.lock here would deadlock the OTA launcher.
    Only the private, pinned current config is repaired, never candidate hooks.
    """
    try:
        release = paths.current.resolve(strict=True)
        if (
            not paths.current.is_symlink()
            or release.parent != paths.releases.resolve(strict=True)
            or not release.is_dir()
        ):
            raise ValueError("invalid release")
        if not terminal_payload_present(release):
            return False
        link = paths.state / "current-compose.json"
        if not link.is_symlink():
            raise ValueError("missing config link")
        target = link.readlink()
        if not target.is_absolute():
            target = link.parent / target
        parent = paths.state / "compose"
        if (
            target.parent != parent
            or parent.resolve(strict=True) != parent
            or target.resolve(strict=True) != target
        ):
            raise ValueError("aliased config")
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_uid != (0 if paths.root == Path("/") else os.geteuid())
                or metadata.st_nlink != 1
                or metadata.st_size > 1024 * 1024
            ):
                raise ValueError("untrusted config")
            document = json.loads(
                stream.read(1024 * 1024 + 1), object_pairs_hook=unique_object
            )
        if not isinstance(document, dict) or document.get("x-robopark-release") != str(
            release
        ):
            raise ValueError("wrong release")
        services = document.get("services")
        if not isinstance(services, dict) or not isinstance(services.get("api"), dict):
            raise TypeError("missing api")
        socket = "/run/robopark-terminal/broker.sock"
        mount = {
            "type": "bind",
            "source": str(paths.root / "run/robopark-terminal"),
            "target": "/run/robopark-terminal",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
        found_mount = False
        for name, service in services.items():
            if not isinstance(service, dict):
                raise TypeError("invalid service")
            environment = service.get("environment", {})
            volumes = service.get("volumes", [])
            if not isinstance(environment, dict) or not isinstance(volumes, list):
                raise TypeError("invalid service config")
            if "TERMINAL_BROKER_SOCKET" in environment and (
                name != "api" or environment["TERMINAL_BROKER_SOCKET"] != socket
            ):
                raise ValueError("conflicting socket")
            for volume in volumes:
                # Catch aliases of the host socket as well as its intended target.
                if "robopark-terminal" not in json.dumps(volume):
                    continue
                if name != "api" or volume != mount or found_mount:
                    raise ValueError("conflicting mount")
                found_mount = True
        api = services["api"]
        environment = api.setdefault("environment", {})
        volumes = api.setdefault("volumes", [])
        changed = "TERMINAL_BROKER_SOCKET" not in environment or not found_mount
        if not changed:
            return False
        environment["TERMINAL_BROKER_SOCKET"] = socket
        if not found_mount:
            volumes.append(mount)
        atomic_write_json(target, document, mode=0o600)
        return True
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        raise ReleaseError("terminal_invalid_compose") from error


def terminal_payload_present(release: Path) -> bool:
    directory = release / "deploy/systemd"
    present = []
    for name in TERMINAL_UNITS:
        path = directory / name
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            present.append(False)
            continue
        if not stat.S_ISREG(mode) or directory.is_symlink():
            raise ReleaseError("terminal_payload_invalid")
        present.append(True)
    if any(present) and not all(present):
        raise ReleaseError("terminal_payload_invalid")
    return all(present)


def quiesce_terminal(paths, runner, *, reason: str) -> None:
    del reason
    installed = paths.root / "etc/systemd/system" / BROKER_UNIT
    if not installed.exists() and not installed.is_symlink():
        return
    runner.run(["systemctl", "stop", BROKER_UNIT], timeout=30)
    value = runner.run(
        ["systemctl", "show", BROKER_UNIT, "--property=ActiveState", "--value"],
        timeout=10,
        capture=True,
    )
    if isinstance(value, bytes):
        value = value.decode("ascii", "strict")
    if str(value).strip() not in {"inactive", "failed"}:
        raise ReleaseError("terminal_stop_failed")
    workers = runner.run(
        [
            "systemctl",
            "list-units",
            "--all",
            "--plain",
            "--no-legend",
            "robopark-terminal-*@*.service",
        ],
        timeout=10,
        capture=True,
    )
    if isinstance(workers, bytes):
        workers = workers.decode("ascii", "strict")
    for line in str(workers).splitlines():
        unit = line.split()[0]
        if not re.fullmatch(
            r"robopark-terminal-(maintenance|root)@[0-9a-f-]{36}\.service", unit
        ):
            raise ReleaseError("terminal_stop_failed")
        runner.run(["systemctl", "stop", unit], timeout=10)
        state = runner.run(
            ["systemctl", "show", unit, "--property=ActiveState", "--value"],
            timeout=10,
            capture=True,
        )
        if isinstance(state, bytes):
            state = state.decode("ascii", "strict")
        if str(state).strip() not in {"inactive", "failed"}:
            raise ReleaseError("terminal_stop_failed")
    runner.run(["systemctl", "disable", BROKER_UNIT], timeout=15)
    runner.run(["systemctl", "stop", TERMINAL_UNITS[0]], timeout=15)


def prepare_terminal_installation(paths, release: Path, runner) -> bool:
    if not terminal_payload_present(release):
        return False
    runner.run(["systemctl", "daemon-reload"], timeout=30)
    try:
        runner.run(["systemctl", "restart", TERMINAL_UNITS[0]], timeout=100)
    except (OSError, RuntimeError, ReleaseError):
        atomic_write_json(
            paths.ops / "public/terminal-capabilities.json",
            {
                "schema": 1,
                "available": False,
                "unavailable_reason": "terminal_setup_failed",
            },
            mode=0o644,
        )
        return False
    return True


def reconcile_terminal_installation(paths, release: Path, runner) -> None:
    if terminal_payload_present(release):
        try:
            runner.run(["systemctl", "enable", "--now", BROKER_UNIT], timeout=30)
        except (OSError, RuntimeError, ReleaseError):
            atomic_write_json(
                paths.ops / "public/terminal-capabilities.json",
                {
                    "schema": 1,
                    "available": False,
                    "unavailable_reason": "terminal_broker_failed",
                },
                mode=0o644,
            )
