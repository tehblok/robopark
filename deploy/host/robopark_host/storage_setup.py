"""Opt-in storage preparation for a fresh host; never partitions or formats.

This module is also bundled into the self-contained OTA bootstrap package.
Only the canonical layout is supported; unknown Docker roots require a separate
maintenance plan. No services are stopped implicitly to make a disk usable.
"""

from __future__ import annotations

import fcntl
import importlib.resources
import json
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .storage_layout import (
    LAYOUT_PATH,
    STORAGE_ROOT,
    TARGETS,
    StorageError,
    inspect_storage,
    load_layout,
    require_storage,
)

_NVME = re.compile(r"/dev/nvme[0-9]+n[0-9]+p[0-9]+\Z")
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_DAEMONS = ("docker.socket", "docker.service", "containerd.service")
_WRITERS = (
    "robopark.service",
    "robopark-updater.service",
    "robopark-commands.service",
    "robopark-backup.service",
    "robopark-doctor.service",
    "robopark-watchdog.service",
    "robopark-bot.service",
    "robopark-tuna.service",
    "robopark-terminal-setup.service",
    "robopark-terminal-broker.service",
    "robopark-terminal-maintenance@.service",
    "robopark-terminal-root@.service",
    "robopark-ai-setup.service",
    "robopark-ai.service",
    "robopark-ai-broker.service",
)


def _path(root: Path, absolute: str) -> Path:
    target = root / absolute.lstrip("/")
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise StorageError("storage_path_unsafe")
    return target


def _run(argv: list[str], *, root: Path) -> str:
    # Synthetic roots may only use an injected executor; never act on the Mac
    # or developer machine while exercising a fake Linux filesystem.
    if root != Path("/"):
        raise StorageError("storage_executor_required")
    try:
        result = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StorageError("storage_setup_command_failed") from exc
    if len(result.stdout) > 65536:
        raise StorageError("storage_setup_command_failed")
    return result.stdout


def _config_roots(root: Path) -> None:
    docker = _path(root, "/etc/docker/daemon.json")
    try:
        if docker.exists():
            if docker.stat().st_size > 65536:
                raise ValueError()
            values = json.loads(docker.read_text())
            if (
                not isinstance(values, dict)
                or values.get("data-root", "/var/lib/docker") != "/var/lib/docker"
            ):
                raise ValueError()
        containerd = _path(root, "/etc/containerd/config.toml")
        if containerd.exists():
            if containerd.stat().st_size > 65536:
                raise ValueError()
            # Preserve all NVIDIA runtime/plugin configuration. Only inspect
            # the top-level root; imported config cannot be verified here.
            header = re.split(r"(?m)^\s*\[", containerd.read_text(), maxsplit=1)[0]
            for key, value in re.findall(r"(?m)^\s*(root|imports)\s*=\s*(.+)$", header):
                value = value.split("#", 1)[0].strip()
                if key == "root" and value not in {
                    '"/var/lib/containerd"',
                    "'/var/lib/containerd'",
                }:
                    raise ValueError()
                if key == "imports" and value != "[]":
                    raise ValueError()
    except (OSError, ValueError, UnicodeError) as exc:
        raise StorageError("storage_custom_container_root") from exc


def _empty(path: Path) -> bool:
    return not path.exists() or path.is_dir() and not any(path.iterdir())


def plan_storage(
    mode: str, device: str | None = None, *, root: Path = Path("/")
) -> dict:
    root = Path(root)
    if mode not in {"nvme-root", "emmc-nvme-data"}:
        raise StorageError("storage_mode_invalid")
    existing = load_layout(root)
    inventory = inspect_storage(root)
    root_mount = inventory["root_mount"]
    root_source = root_mount["source"].split("[", 1)[0]
    if mode == "nvme-root":
        device = device or root_source
        if not _NVME.fullmatch(root_source) or root_source != device:
            raise StorageError("storage_root_not_nvme")
    elif not re.fullmatch(r"/dev/mmcblk[0-9]+p[0-9]+", root_source):
        raise StorageError("storage_root_not_emmc")
    if not isinstance(device, str) or not _NVME.fullmatch(device):
        raise StorageError("storage_device_invalid")
    matches = [row for row in inventory["block_devices"] if row["path"] == device]
    if len(matches) != 1:
        raise StorageError("storage_device_invalid")
    block = matches[0]
    if block["type"] != "part" or block["fstype"] != "ext4" or block["ro"]:
        raise StorageError("storage_ext4_required")
    if not _UUID.fullmatch(block.get("uuid") or "") or not _UUID.fullmatch(
        root_mount.get("uuid") or ""
    ):
        raise StorageError("storage_uuid_missing")
    layout = {
        "schema": 1,
        "mode": mode,
        "device": device,
        "uuid": block["uuid"],
        "root_uuid": root_mount["uuid"],
        "state": "preparing",
        "min_free_bytes": 64 * 1024**2,
    }
    if existing and {**existing, "state": "preparing"} != layout:
        raise StorageError("storage_layout_conflict")
    allowed = (
        {"/"}
        if mode == "nvme-root"
        else ({STORAGE_ROOT, *TARGETS.values()} if existing else set())
    )
    if any(point and point not in allowed for point in block.get("mountpoints", [])):
        raise StorageError("storage_device_in_use")
    # A preparing manifest is the durable ownership record for resumed steps.
    # It never authorizes adopting unknown directories on first preparation.
    if not existing:
        for destination in (*TARGETS.values(), "/etc/robopark", STORAGE_ROOT):
            if not _empty(_path(root, destination)):
                raise StorageError("storage_target_not_empty")
    _config_roots(root)
    return {
        "schema": 1,
        "layout": layout,
        "device_bytes": block.get("size"),
        "root_mount": root_mount,
        "mounts": []
        if mode == "nvme-root"
        else [
            {"source": f"{STORAGE_ROOT}/{name}", "target": target}
            for name, target in TARGETS.items()
        ],
        "confirmation": f"PREPARE {mode} {block['uuid']}",
        "notes": [
            "No partitioning or formatting.",
            "Existing data is never adopted automatically.",
            "Verify filesystem capacity after vendor flashing; SSD capacity is not rootfs capacity.",
        ],
    }


def _unit_name(path: str) -> str:
    return path.strip("/").replace("-", r"\x2d").replace("/", "-") + ".mount"


def storage_units(layout: dict) -> dict[str, str]:
    result = {}
    mounts = []
    if layout["mode"] == "emmc-nvme-data":
        base_unit = _unit_name(STORAGE_ROOT)
        sources = [
            (f"/dev/disk/by-uuid/{layout['uuid']}", STORAGE_ROOT, "ext4", "defaults")
        ]
        sources += [
            (f"{STORAGE_ROOT}/{key}", target, "none", "bind")
            for key, target in TARGETS.items()
        ]
        for source, target, kind, options in sources:
            name = _unit_name(target)
            dependencies = (
                ""
                if target == STORAGE_ROOT
                else f"Requires={base_unit}\nAfter={base_unit}\nBindsTo={base_unit}\n"
            )
            result[name] = (
                "[Unit]\nDescription=Robopark working storage\nDefaultDependencies=no\n"
                "After=local-fs-pre.target\nConflicts=umount.target\nBefore=umount.target\nJobTimeoutSec=20s\n"
                + dependencies
                + f"\n[Mount]\nWhat={source}\nWhere={target}\nType={kind}\nOptions={options}\nTimeoutSec=15s\n"
            )
            mounts.append(name)
    target_deps = (
        ""
        if not mounts
        else "Requires="
        + " ".join(mounts)
        + "\nAfter="
        + " ".join(mounts)
        + "\nBindsTo="
        + " ".join(mounts)
        + "\n"
    )
    result["robopark-storage.target"] = (
        "[Unit]\nDescription=Robopark verified storage mounts\n" + target_deps
    )
    dependencies = "[Unit]\nRequires=robopark-storage.target\nAfter=robopark-storage.target\nBindsTo=robopark-storage.target\n"
    # This dependency finishes before ANY vendor ExecStartPre/ExecStopPost
    # code can run. It is not retained active, so new start jobs recheck it.
    result["robopark-storage-check.service"] = dependencies + (
        "Description=Validate Robopark storage before service activation\n"
        "\n[Service]\nType=oneshot\n"
        "ExecStart=/usr/bin/python3 -I /usr/lib/robopark-storage/storage_layout.py check --presence-only\n"
        "TimeoutStartSec=20\nNoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\nUMask=0077\n"
    )
    dependencies += "Requires=robopark-storage-check.service\nAfter=robopark-storage-check.service\n"
    for unit in (*_DAEMONS, *_WRITERS):
        section = "Socket" if unit.endswith(".socket") else "Service"
        result[f"{unit}.d/50-robopark-storage.conf"] = dependencies + (
            f"\n[{section}]\nExecStartPre=+/usr/bin/python3 -I /usr/lib/robopark-storage/storage_layout.py check --presence-only\n"
        )
        if unit in {"robopark-updater.service", "robopark-commands.service"}:
            result[f"{unit}.d/50-robopark-storage.conf"] += (
                "ReadWritePaths=/usr/lib/robopark-storage\n"
            )
    result["robopark-storage-watchdog.service"] = (
        "[Unit]\nDescription=Check Robopark storage independently of the application\n"
        "\n[Service]\nType=oneshot\nExecStart=/usr/bin/python3 -I /usr/lib/robopark-storage/storage_watchdog.py\n"
        "TimeoutStartSec=120\nNoNewPrivileges=yes\nPrivateTmp=yes\nProtectHome=yes\nUMask=0077\n"
    )
    result["robopark-storage-watchdog.timer"] = (
        "[Unit]\nDescription=Check Robopark working disk\n\n[Timer]\nOnBootSec=45s\nOnUnitActiveSec=15s\nAccuracySec=1s\n"
        "\n[Install]\nWantedBy=timers.target\n"
    )
    return result


def _atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _lock(root: Path):
    path = _path(root, "/run/lock/robopark-storage.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise StorageError("storage_busy") from exc
    finally:
        os.close(fd)


def _require_stopped(root: Path) -> None:
    for unit in _DAEMONS:
        if _run(
            ["systemctl", "show", unit, "--property=ActiveState", "--value"], root=root
        ).strip() not in {"", "inactive", "failed"}:
            raise StorageError("storage_daemon_running")
    for process in (root / "proc").glob("[0-9]*/comm"):
        try:
            name = process.read_text().strip()
        except FileNotFoundError:
            continue
        if name in {"dockerd", "containerd", "runc"} or name.startswith(
            "containerd-shim"
        ):
            raise StorageError("storage_daemon_running")
    # Reject command-line overrides rather than silently preserving eMMC data.
    for unit in ("docker.service", "containerd.service"):
        command = _run(
            ["systemctl", "show", unit, "--property=ExecStart", "--value"], root=root
        )
        if any(
            flag in command
            for flag in (
                "--data-root",
                "--root",
                " -g ",
                "--config",
                " -c ",
                "$",
            )
        ):
            raise StorageError("storage_custom_container_root")


def _probe_empty_filesystem(layout: dict, *, root: Path) -> None:
    probe = _path(root, "/run/robopark-storage-probe")
    if not _empty(probe):
        raise StorageError("storage_probe_conflict")
    probe.mkdir(parents=True, exist_ok=True, mode=0o700)
    _run(
        [
            "mount",
            "-t",
            "ext4",
            "-o",
            "ro,noload,nodev,nosuid,noexec",
            f"/dev/disk/by-uuid/{layout['uuid']}",
            str(probe),
        ],
        root=root,
    )
    try:
        for child in probe.iterdir():
            if (
                child.name != "lost+found"
                or child.is_symlink()
                or not child.is_dir()
                or any(child.iterdir())
            ):
                raise StorageError("storage_device_not_empty")
    finally:
        _run(["umount", str(probe)], root=root)
        probe.rmdir()


def _activate_layout(layout: dict, *, root: Path) -> None:
    _run(["systemctl", "daemon-reload"], root=root)
    if layout["mode"] == "emmc-nvme-data":
        _path(root, STORAGE_ROOT).mkdir(parents=True, exist_ok=True, mode=0o700)
        _run(["systemctl", "start", _unit_name(STORAGE_ROOT)], root=root)
        # Confirm the mount before creating directories; never create these on
        # the root filesystem as a fallback after an unsuccessful mount.
        base = [
            m for m in inspect_storage(root)["mounts"] if m["target"] == STORAGE_ROOT
        ]
        if (
            len(base) != 1
            or base[0]["uuid"] != layout["uuid"]
            or base[0]["fsroot"] != "/"
            or base[0]["source"] != layout["device"]
            or base[0]["fstype"] != "ext4"
            or base[0]["device_type"] != "nvme"
            or "rw" not in base[0]["options"]
            or "ro" in base[0]["options"]
        ):
            raise StorageError("storage_mount_missing")
        # The earlier read-only probe suppressed ext4 journal replay. A RW
        # mount can make recovered files visible; do not adopt them. On retry
        # only our already-created, still-empty directories are expected.
        for child in _path(root, STORAGE_ROOT).iterdir():
            if (
                child.name not in {*TARGETS, "lost+found"}
                or child.is_symlink()
                or not child.is_dir()
                or any(child.iterdir())
            ):
                raise StorageError("storage_device_not_empty")
        for name, destination in TARGETS.items():
            _path(root, f"{STORAGE_ROOT}/{name}").mkdir(mode=0o700, exist_ok=True)
            _path(root, destination).mkdir(parents=True, exist_ok=True, mode=0o700)
        _run(
            [
                "systemctl",
                "start",
                *[_unit_name(target) for target in TARGETS.values()],
            ],
            root=root,
        )
    require_storage(root, require_layout=True, allow_preparing=True)


def prepare_storage(
    mode: str, device: str | None = None, *, confirmation: str, root: Path = Path("/")
) -> dict:
    root = Path(root)
    if root == Path("/") and os.geteuid() != 0:
        raise StorageError("root_required")
    plan = plan_storage(mode, device, root=root)
    if confirmation != plan["confirmation"]:
        raise StorageError("storage_confirmation_required")
    with _lock(root):
        # Re-probe while locked and bind confirmation to the current UUID.
        current = plan_storage(mode, device, root=root)
        if current["layout"] != plan["layout"]:
            raise StorageError("storage_plan_changed")
        old = load_layout(root)
        if old and old["state"] == "ready":
            result = require_storage(root, require_layout=True)
            # A power cut can occur after the durable ready manifest but
            # before systemd enables the independent watchdog.
            _run(
                ["systemctl", "enable", "--now", "robopark-storage-watchdog.timer"],
                root=root,
            )
            return result
        _require_stopped(root)
        layout = plan["layout"]
        units = storage_units(layout)
        for name, content in units.items():
            target = _path(root, "/etc/systemd/system/" + name)
            if target.exists() and target.read_text() != content:
                raise StorageError("storage_unit_conflict")
        if layout["mode"] == "emmc-nvme-data" and old is None:
            _probe_empty_filesystem(layout, root=root)
        # Everything from here is resumable. A crash leaves preparing, which
        # the independently installed guard rejects before starting writers.
        for filename in ("storage_layout.py", "storage_watchdog.py"):
            source = importlib.resources.files(__package__).joinpath(filename)
            _atomic(
                _path(root, "/usr/lib/robopark-storage/" + filename),
                source.read_bytes(),
                0o644,
            )
        manifest = _path(root, LAYOUT_PATH)
        manifest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic(manifest, (json.dumps(layout, sort_keys=True) + "\n").encode(), 0o600)
        for name, content in units.items():
            _atomic(_path(root, "/etc/systemd/system/" + name), content.encode(), 0o644)
        _activate_layout(layout, root=root)
        ready = {**layout, "state": "ready"}
        _atomic(manifest, (json.dumps(ready, sort_keys=True) + "\n").encode(), 0o600)
        _run(
            ["systemctl", "enable", "--now", "robopark-storage-watchdog.timer"],
            root=root,
        )
        return require_storage(root, require_layout=True)


def refresh_storage_guard(root: Path, release: Path) -> None:
    """Refresh the independent copy only from an already verified release."""
    if load_layout(root) is None:
        return
    require_storage(root, require_layout=True, check_space=False)
    contents = {}
    for name in ("storage_layout.py", "storage_watchdog.py"):
        source = release / "deploy/host/robopark_host" / name
        try:
            if (
                source.is_symlink()
                or not source.is_file()
                or source.stat().st_size > 256 * 1024
            ):
                raise ValueError()
            contents[name] = source.read_bytes()
            compile(contents[name], name, "exec")
        except (OSError, ValueError, SyntaxError) as exc:
            raise StorageError("storage_guard_payload_invalid") from exc
    for name, content in contents.items():
        _atomic(_path(root, "/usr/lib/robopark-storage/" + name), content, 0o644)
