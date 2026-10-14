#!/usr/bin/env python3
"""Bounded IPv4 Wi-Fi recovery for a configured NetworkManager profile."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/etc/robopark/network-recovery.json")
RUNTIME_DIR = Path("/run/robopark-network")
PROBES = ("https://api.tuna.am/", "https://api.telegram.org/")
FAILURE_THRESHOLD = 3
RECOVERY_COOLDOWN_SECONDS = 180.0
INTERFACE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}\Z")
COMMAND_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}


@dataclass(frozen=True)
class RecoveryConfig:
    interface: str
    connection_uuid: str


def read_config(path: Path) -> tuple[RecoveryConfig | None, str]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            return None, "config_invalid"
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "config_missing"
    except (OSError, UnicodeError, ValueError):
        return None, "config_invalid"

    if not isinstance(raw, dict):
        return None, "config_invalid"
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        return None, "config_invalid"
    if not enabled:
        return None, "disabled"

    interface = raw.get("interface")
    connection_uuid = raw.get("connection_uuid")
    if not isinstance(interface, str) or not INTERFACE_PATTERN.fullmatch(interface):
        return None, "config_invalid"
    if not isinstance(connection_uuid, str):
        return None, "config_invalid"
    try:
        normalized_uuid = str(uuid.UUID(connection_uuid))
    except (ValueError, AttributeError):
        return None, "config_invalid"
    return RecoveryConfig(interface, normalized_uuid), ""


def _run(argv: list[str], *, timeout: int, capture: bool = False):
    return subprocess.run(
        argv,
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=capture,
        timeout=timeout,
        env=COMMAND_ENV,
    )


def probe(config: RecoveryConfig, url: str) -> str:
    """Return an HTTP status code, or 000 when no IPv4 response arrived."""
    try:
        result = _run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--interface",
                config.interface,
                "--ipv4",
                "--max-time",
                "8",
                "--connect-timeout",
                "4",
                url,
            ],
            timeout=12,
            capture=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "000"
    code = result.stdout.strip()
    if len(code) == 3 and code.isdigit() and code != "000":
        return code
    return "000"


def check_connectivity(config: RecoveryConfig) -> tuple[bool, list[str]]:
    codes = [probe(config, url) for url in PROBES]
    return any(code != "000" for code in codes), codes


def default_state() -> dict[str, Any]:
    return {"failures": 0, "next_allowed_monotonic": 0.0}


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        failures = max(0, int(raw.get("failures", 0)))
        next_allowed = max(0.0, float(raw.get("next_allowed_monotonic", 0.0)))
        return {
            "failures": failures,
            "next_allowed_monotonic": next_allowed,
        }
    except (FileNotFoundError, OSError, UnicodeError, ValueError, TypeError):
        return default_state()


def save_state(path: Path, state: dict[str, Any]) -> None:
    temporary_path = path.with_name(path.name + ".tmp")
    descriptor = os.open(
        temporary_path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, separators=(",", ":"), sort_keys=True)
            state_file.write("\n")
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def next_state(
    state: dict[str, Any], healthy: bool, now: float
) -> tuple[dict[str, Any], bool]:
    next_allowed = max(0.0, float(state.get("next_allowed_monotonic", 0.0)))
    if healthy:
        return {
            "failures": 0,
            "next_allowed_monotonic": next_allowed,
        }, False

    failures = max(0, int(state.get("failures", 0))) + 1
    updated = {
        "failures": failures,
        "next_allowed_monotonic": next_allowed,
    }
    return updated, failures >= FAILURE_THRESHOLD and now >= next_allowed


def _capture_nmcli(arguments: list[str], timeout: int = 12) -> tuple[int, str]:
    try:
        result = _run(["nmcli", *arguments], timeout=timeout, capture=True)
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except OSError:
        return 127, ""


def connection_guard(config: RecoveryConfig) -> str | None:
    radio_code, radio = _capture_nmcli(
        ["--terse", "--fields", "WIFI", "general"]
    )
    if radio_code != 0:
        return "networkmanager_unavailable"
    if radio == "disabled":
        return "wifi_radio_off"
    if radio != "enabled":
        return "networkmanager_unavailable"

    active_code, active = _capture_nmcli(
        [
            "--terse",
            "--fields",
            "UUID,DEVICE",
            "connection",
            "show",
            "--active",
        ]
    )
    if active_code != 0:
        return "networkmanager_unavailable"

    active_uuids = []
    for line in active.splitlines():
        connection_uuid, separator, interface = line.partition(":")
        if separator and interface == config.interface:
            active_uuids.append(connection_uuid)
    if active_uuids and any(
        active_uuid != config.connection_uuid for active_uuid in active_uuids
    ):
        return "active_wifi_changed"
    return None


def _run_nmcli(arguments: list[str], timeout: int) -> int:
    try:
        return _run(["nmcli", *arguments], timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 127


def recover_connection(config: RecoveryConfig) -> tuple[int, int]:
    disconnect_code = _run_nmcli(
        ["--wait", "10", "device", "disconnect", config.interface], timeout=15
    )
    up_code = _run_nmcli(
        [
            "--wait",
            "45",
            "connection",
            "up",
            "uuid",
            config.connection_uuid,
            "ifname",
            config.interface,
        ],
        timeout=50,
    )
    return disconnect_code, up_code


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True), flush=True)


def run(
    *,
    check_only: bool = False,
    config_path: Path = CONFIG_PATH,
    runtime_dir: Path = RUNTIME_DIR,
) -> int:
    config, skip_reason = read_config(config_path)
    if config is None:
        emit({"reason": skip_reason, "state": "skipped"})
        return 0

    if check_only:
        healthy, codes = check_connectivity(config)
        emit({"codes": codes, "healthy": healthy, "mode": "check-only"})
        return 0 if healthy else 1

    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(runtime_dir, 0o700)
    state_path = runtime_dir / "recovery.json"
    lock_descriptor = os.open(
        runtime_dir / "recovery.lock", os.O_WRONLY | os.O_CREAT, 0o600
    )
    os.fchmod(lock_descriptor, 0o600)
    with os.fdopen(lock_descriptor, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        healthy, codes = check_connectivity(config)
        now = time.monotonic()
        state, should_recover = next_state(load_state(state_path), healthy, now)

        if healthy:
            save_state(state_path, state)
            emit({"codes": codes, "failures": 0, "healthy": True})
            return 0

        if not should_recover:
            save_state(state_path, state)
            emit(
                {
                    "codes": codes,
                    "failures": state["failures"],
                    "healthy": False,
                    "recovery": "deferred",
                }
            )
            return 0

        guard_reason = connection_guard(config)
        if guard_reason is not None:
            save_state(state_path, state)
            emit(
                {
                    "codes": codes,
                    "failures": state["failures"],
                    "healthy": False,
                    "reason": guard_reason,
                    "recovery": "skipped",
                }
            )
            return 0

        state["next_allowed_monotonic"] = now + RECOVERY_COOLDOWN_SECONDS
        save_state(state_path, state)
        disconnect_code, up_code = recover_connection(config)
        emit(
            {
                "codes": codes,
                "disconnect_code": disconnect_code,
                "failures": state["failures"],
                "healthy": False,
                "recovery": "attempted",
                "up_code": up_code,
            }
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    arguments = parser.parse_args(argv)
    return run(check_only=arguments.check_only)


if __name__ == "__main__":
    raise SystemExit(main())
