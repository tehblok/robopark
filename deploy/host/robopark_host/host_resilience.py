"""Idempotent root setup for AGX network and host resilience policy."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .network_recovery import INTERFACE_PATTERN, RecoveryConfig, read_config

NETWORK_CONFIG = Path("/etc/robopark/network-recovery.json")
NETWORK_HELPER = Path("/usr/local/libexec/robopark-network-recovery.py")
SLEEP_POLICY = Path("/etc/systemd/sleep.conf.d/90-robopark-no-sleep.conf")
JOURNAL_POLICY = Path(
    "/etc/systemd/journald.conf.d/90-robopark-persistent.conf"
)
BOOT_DROPIN = "10-robopark-boot-order.conf"
BOOT_UNITS = (
    "robopark-commands.path",
    "robopark-bot.path",
    "robopark-storage-check.service",
)
SLEEP_TARGETS = (
    "sleep.target",
    "suspend.target",
    "hibernate.target",
    "hybrid-sleep.target",
    "suspend-then-hibernate.target",
)
SLEEP_CONTENT = (
    b"[Sleep]\n"
    b"AllowSuspend=no\n"
    b"AllowHibernation=no\n"
    b"AllowSuspendThenHibernate=no\n"
    b"AllowHybridSleep=no\n"
)
JOURNAL_CONTENT = (
    b"[Journal]\n"
    b"Storage=persistent\n"
    b"SystemMaxUse=256M\n"
    b"RuntimeMaxUse=64M\n"
    b"MaxRetentionSec=14day\n"
)
DROPIN_CONTENT = (
    b"[Unit]\n"
    b"DefaultDependencies=no\n"
    b"Requires=sysinit.target\n"
    b"After=sysinit.target\n"
    b"Conflicts=shutdown.target\n"
    b"Before=shutdown.target\n"
)
COMMAND_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}
Runner = Callable[..., subprocess.CompletedProcess]


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


def _invoke(
    runner: Runner, argv: list[str], *, timeout: int, capture: bool = False
) -> subprocess.CompletedProcess:
    result = runner(argv, timeout=timeout, capture=capture)
    if result.returncode != 0:
        raise RuntimeError("host_resilience_command_failed")
    return result


def _rooted(root: Path, target: Path) -> Path:
    return root / target.relative_to("/")


def _managed_paths(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    regular_targets = [
        _rooted(root, NETWORK_CONFIG),
        _rooted(root, NETWORK_HELPER),
        _rooted(root, SLEEP_POLICY),
        _rooted(root, JOURNAL_POLICY),
    ]
    regular_targets.extend(
        root / "etc/systemd/system" / f"{unit}.d" / BOOT_DROPIN
        for unit in BOOT_UNITS
    )
    mask_targets = [
        root / "etc/systemd/system" / target for target in SLEEP_TARGETS
    ]
    directories = sorted(
        {target.parent for target in (*regular_targets, *mask_targets)},
        key=lambda path: (len(path.parts), str(path)),
    )
    return regular_targets, mask_targets, directories


def _validate_directory_chain(root: Path, directory: Path) -> None:
    if directory != root and root not in directory.parents:
        raise ValueError("unsafe_resilience_path")
    for candidate in (directory, *directory.parents):
        if candidate == root.parent:
            break
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if candidate == root:
                raise ValueError("unsafe_resilience_path")
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("unsafe_resilience_path")
        if candidate == root:
            break


def validate_paths(root: Path) -> None:
    regular_targets, mask_targets, directories = _managed_paths(root)
    for directory in directories:
        _validate_directory_chain(root, directory)
    for target in regular_targets:
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe_resilience_path")
    for target in mask_targets:
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISLNK(info.st_mode) or os.readlink(target) != "/dev/null":
            raise ValueError("unsafe_resilience_path")


def _ensure_directories(root: Path) -> None:
    _, _, directories = _managed_paths(root)
    for directory in directories:
        directory.mkdir(mode=0o755, parents=True, exist_ok=True)
        _validate_directory_chain(root, directory)


def _atomic_write(target: Path, content: bytes, mode: int) -> bool:
    try:
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe_resilience_path")
        if target.read_bytes() == content and stat.S_IMODE(info.st_mode) == mode:
            return False
    except FileNotFoundError:
        pass

    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}."
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_create(target: Path, content: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}."
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError("invalid_network_recovery_config") from exc
        directory_descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _ensure_mask(target: Path) -> bool:
    try:
        info = target.lstat()
        if stat.S_ISLNK(info.st_mode) and os.readlink(target) == "/dev/null":
            return False
        raise ValueError("unsafe_resilience_path")
    except FileNotFoundError:
        pass

    try:
        target.symlink_to("/dev/null")
        return True
    except FileExistsError:
        try:
            if target.is_symlink() and os.readlink(target) == "/dev/null":
                return False
        except OSError:
            pass
        raise ValueError("unsafe_resilience_path")


def _validate_release(release_root: Path) -> Path:
    try:
        release_info = release_root.lstat()
    except FileNotFoundError as exc:
        raise ValueError("invalid_resilience_release") from exc
    if not stat.S_ISDIR(release_info.st_mode) or release_root.resolve() != release_root:
        raise ValueError("invalid_resilience_release")
    source = release_root / "deploy/host/robopark_host/network_recovery.py"
    try:
        source_info = source.lstat()
    except FileNotFoundError as exc:
        raise ValueError("invalid_resilience_release") from exc
    if (
        not stat.S_ISREG(source_info.st_mode)
        or source_info.st_nlink != 1
        or source_info.st_size > 1024 * 1024
    ):
        raise ValueError("invalid_resilience_release")
    return source


def _existing_config(path: Path) -> tuple[RecoveryConfig | None, bool]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, False
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size > 4096
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("invalid_network_recovery_config")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("invalid_network_recovery_config") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("enabled", True), bool):
        raise ValueError("invalid_network_recovery_config")  # noqa: TRY004
    if raw.get("enabled", True) is False:
        return None, True
    config, reason = read_config(path)
    if config is None or reason:
        raise ValueError("invalid_network_recovery_config")
    return config, True


def _active_wifi(runner: Runner) -> list[RecoveryConfig]:
    try:
        result = runner(
            [
                "nmcli",
                "--terse",
                "--fields",
                "UUID,TYPE,DEVICE",
                "connection",
                "show",
                "--active",
            ],
            timeout=12,
            capture=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []

    found = []
    for line in result.stdout.splitlines():
        fields = line.split(":")
        if len(fields) != 3 or fields[1] not in {"802-11-wireless", "wifi"}:
            continue
        candidate = {"connection_uuid": fields[0], "interface": fields[2]}
        try:
            normalized = RecoveryConfig(
                interface=candidate["interface"],
                connection_uuid=str(uuid.UUID(candidate["connection_uuid"])),
            )
        except (ValueError, AttributeError):
            continue
        if INTERFACE_PATTERN.fullmatch(normalized.interface) is None:
            continue
        found.append(normalized)
    return found


def _write_new_config(path: Path, config: RecoveryConfig) -> None:
    content = (
        json.dumps(
            {
                "connection_uuid": config.connection_uuid,
                "enabled": True,
                "interface": config.interface,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    _atomic_create(path, content, 0o600)


def _configure_wifi(
    config: RecoveryConfig, active: list[RecoveryConfig], runner: Runner
) -> None:
    _invoke(
        runner,
        [
            "nmcli",
            "connection",
            "modify",
            "uuid",
            config.connection_uuid,
            "connection.autoconnect",
            "yes",
            "connection.autoconnect-retries",
            "0",
            "802-11-wireless.powersave",
            "2",
            "ipv4.may-fail",
            "no",
            "ipv4.dhcp-timeout",
            "45",
        ],
        timeout=20,
    )
    if config in active:
        _invoke(
            runner,
            ["iw", "dev", config.interface, "set", "power_save", "off"],
            timeout=10,
        )


def setup(
    *,
    root: Path = Path("/"),
    release_root: Path | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    root = Path(root)
    if root != Path("/"):
        if os.environ.get("ROBOPARK_TESTING") != "1":
            raise ValueError("resilience_test_root_forbidden")
    elif os.geteuid() != 0 or not sys.platform.startswith("linux"):
        raise ValueError("resilience_requires_linux_root")

    if release_root is None:
        release_root = Path(__file__).resolve().parents[3]
    else:
        release_root = Path(release_root)
    source = _validate_release(release_root)
    validate_paths(root)

    config_path = _rooted(root, NETWORK_CONFIG)
    config, config_exists = _existing_config(config_path)
    _ensure_directories(root)

    active = []
    if not config_exists or config is not None:
        active = _active_wifi(runner)
    config_created = False
    if not config_exists and len(active) == 1:
        config = active[0]
        _write_new_config(config_path, config)
        config_created = True

    changed = int(config_created)
    if _atomic_write(_rooted(root, NETWORK_HELPER), source.read_bytes(), 0o755):
        changed += 1
    if _atomic_write(_rooted(root, SLEEP_POLICY), SLEEP_CONTENT, 0o644):
        changed += 1
    journal_changed = _atomic_write(
        _rooted(root, JOURNAL_POLICY), JOURNAL_CONTENT, 0o644
    )
    changed += int(journal_changed)
    for unit in BOOT_UNITS:
        target = root / "etc/systemd/system" / f"{unit}.d" / BOOT_DROPIN
        if _atomic_write(target, DROPIN_CONTENT, 0o644):
            changed += 1
    for target_name in SLEEP_TARGETS:
        target = root / "etc/systemd/system" / target_name
        if _ensure_mask(target):
            changed += 1

    if config is not None:
        _configure_wifi(config, active, runner)

    if journal_changed:
        _invoke(
            runner,
            ["systemd-tmpfiles", "--create", "--prefix=/var/log/journal"],
            timeout=30,
        )
        _invoke(
            runner,
            ["systemctl", "restart", "systemd-journald.service"],
            timeout=30,
        )
        _invoke(runner, ["journalctl", "--flush"], timeout=30)

    _invoke(runner, ["systemctl", "daemon-reload"], timeout=30)
    _invoke(
        runner,
        [
            "systemctl",
            "enable",
            "--now",
            "robopark-network-recovery.timer",
        ],
        timeout=60,
    )
    return {
        "changed": changed,
        "network_config": "enabled" if config is not None else "skipped",
        "state": "ready",
    }
