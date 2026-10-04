"""Read-only validation for the Robopark host storage layout.

This module intentionally has no package-local imports.  The installed copy can
therefore run with ``python3 -I`` before any application path is available.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

LAYOUT_PATH = "/etc/robopark-storage/layout.json"
STORAGE_ROOT = "/srv/robopark-storage"
TARGETS = {
    "application": "/opt/robopark",
    "state": "/var/lib/robopark",
    "docker": "/var/lib/docker",
    "containerd": "/var/lib/containerd",
    "logs": "/var/log/robopark",
    "backups": "/var/backups/robopark",
}

_LAYOUT_FIELDS = {
    "schema",
    "mode",
    "uuid",
    "root_uuid",
    "device",
    "state",
    "min_free_bytes",
}
_MAX_LAYOUT_BYTES = 16 * 1024
_MAX_MOUNTINFO_BYTES = 1024 * 1024
_MAX_COMMAND_BYTES = 2 * 1024 * 1024
_MAX_CONTAINER_CONFIG_BYTES = 64 * 1024
_MIN_FREE_BYTES = 67_108_864
_UUID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
_NVME_PARTITION_RE = re.compile(r"/dev/nvme[0-9]+n[0-9]+p[0-9]+\Z")


class StorageError(RuntimeError):
    """A storage guard failure whose code is safe to show to an operator."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _manifest_path(root: Path) -> Path:
    return root / LAYOUT_PATH.lstrip("/")


def _validate_manifest_file(root: Path, path: Path) -> None:
    expected_uid = 0 if root == Path("/") else os.getuid()
    current = root
    for part in Path(LAYOUT_PATH).parts[1:-1]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise StorageError("layout_unsafe") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise StorageError("layout_unsafe")
        if info.st_uid != expected_uid or info.st_mode & 0o022:
            raise StorageError("layout_unsafe")
    try:
        info = path.lstat()
    except OSError as exc:
        raise StorageError("layout_unsafe") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != expected_uid
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise StorageError("layout_unsafe")


def load_layout(root: Path = Path("/")) -> dict[str, Any] | None:
    """Load and strictly validate the installed storage manifest."""

    root = Path(root)
    path = _manifest_path(root)
    if not path.exists() and not path.is_symlink():
        return None
    _validate_manifest_file(root, path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            expected_uid = 0 if root == Path("/") else os.getuid()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != expected_uid
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise StorageError("layout_unsafe")
            if info.st_size > _MAX_LAYOUT_BYTES:
                raise ValueError("invalid size")
            raw = stream.read(_MAX_LAYOUT_BYTES + 1)
        if len(raw) > _MAX_LAYOUT_BYTES:
            raise ValueError("invalid size")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except StorageError:
        raise
    except (
        OSError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
    ) as exc:
        raise StorageError("layout_invalid") from exc
    if not isinstance(value, dict) or set(value) != _LAYOUT_FIELDS:
        raise StorageError("layout_invalid")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise StorageError("layout_invalid")
    if value["mode"] not in {"nvme-root", "emmc-nvme-data"}:
        raise StorageError("layout_invalid")
    for name in ("uuid", "root_uuid"):
        if not isinstance(value[name], str) or not _UUID_RE.fullmatch(value[name]):
            raise StorageError("layout_invalid")
    if not isinstance(value["device"], str) or not _NVME_PARTITION_RE.fullmatch(
        value["device"]
    ):
        raise StorageError("layout_invalid")
    if value["state"] not in {"preparing", "ready"}:
        raise StorageError("layout_invalid")
    minimum = value["min_free_bytes"]
    if type(minimum) is not int or minimum != _MIN_FREE_BYTES:
        raise StorageError("layout_invalid")
    return value


def _configuration_path(root: Path, absolute: str) -> Path:
    target = root / absolute.lstrip("/")
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise StorageError("storage_custom_container_root")
    return target


def _read_container_config(root: Path, absolute: str) -> str | None:
    path = _configuration_path(root, absolute)
    if not path.exists():
        return None
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_CONTAINER_CONFIG_BYTES:
            raise OSError("unsafe container configuration")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StorageError("storage_custom_container_root") from exc


def _simple_toml_key(raw: str) -> str:
    raw = raw.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        return raw
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        value = raw[1:-1]
        if re.fullmatch(r"[A-Za-z0-9_-]+", value):
            return value
    # Escaped, dotted, or otherwise complex top-level keys cannot safely be
    # interpreted by the Python 3.10 standalone guard.
    raise ValueError("unsupported TOML key")


def require_container_config_files(root: Path = Path("/")) -> None:
    """Require canonical Docker/containerd roots without changing vendor config."""

    root = Path(root)
    try:
        docker = _read_container_config(root, "/etc/docker/daemon.json")
        if docker is not None:
            values = json.loads(docker, object_pairs_hook=_unique_object)
            if (
                not isinstance(values, dict)
                or values.get("data-root", "/var/lib/docker") != "/var/lib/docker"
            ):
                raise ValueError("custom Docker root")
        containerd = _read_container_config(root, "/etc/containerd/config.toml")
        if containerd is not None:
            header = re.split(r"(?m)^\s*\[", containerd, maxsplit=1)[0]
            seen: set[str] = set()
            for line in header.splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                raw_key, separator, value = line.partition("=")
                if not separator:
                    raise ValueError("unsupported TOML assignment")
                key = _simple_toml_key(raw_key)
                if key not in {"root", "imports"}:
                    continue
                if key in seen:
                    raise ValueError("duplicate container root key")
                seen.add(key)
                value = value.split("#", 1)[0].strip()
                if key == "root" and value not in {
                    '"/var/lib/containerd"',
                    "'/var/lib/containerd'",
                }:
                    raise ValueError("custom containerd root")
                if key == "imports" and value != "[]":
                    raise ValueError("unverifiable containerd imports")
    except (ValueError, RecursionError, json.JSONDecodeError) as exc:
        raise StorageError("storage_custom_container_root") from exc


def _run_text(argv: list[str], *, root: Path) -> str:
    if root != Path("/"):
        raise StorageError("storage_probe_failed")
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
        if (
            result.returncode != 0
            or len(result.stdout.encode("utf-8")) > _MAX_CONTAINER_CONFIG_BYTES
        ):
            raise ValueError("probe command failed")
        return result.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise StorageError("storage_probe_failed") from exc


def require_container_daemon_commands(root: Path = Path("/"), *, run=None) -> None:
    """Reject systemd command lines that can redirect persistent state."""

    root = Path(root)
    if run is None:
        if root != Path("/"):
            return
        run = _run_text
    for unit in ("docker.service", "containerd.service"):
        command = run(
            ["systemctl", "show", unit, "--property=ExecStart", "--value"],
            root=root,
        )
        long_override = re.search(
            r"(?:^|[\s;])(?:--data-root|--graph|--root|--config(?:-file)?)(?=$|[\s=;])",
            command,
        )
        short_override = re.search(r"(?:^|[\s;])-(?:g|c)", command)
        if long_override or short_override or "$" in command:
            raise StorageError("storage_custom_container_root")


def require_container_configuration(root: Path = Path("/"), *, run=None) -> None:
    """Validate container roots without connecting to or starting a daemon."""

    require_container_config_files(root)
    require_container_daemon_commands(root, run=run)


def _unescape_mount(value: str) -> str:
    return re.sub(
        r"\\([0-7]{3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )


def parse_mountinfo(text: str) -> list[dict[str, Any]]:
    """Parse a bounded Linux mountinfo projection into JSON-safe records."""

    if not isinstance(text, str) or len(text.encode("utf-8")) > _MAX_MOUNTINFO_BYTES:
        raise StorageError("storage_probe_failed")
    records: list[dict[str, Any]] = []
    try:
        for line in text.splitlines():
            fields = line.split()
            separator = fields.index("-")
            if separator < 6 or len(fields) < separator + 4:
                raise ValueError("short mountinfo record")
            options = sorted(
                set(fields[5].split(",")) | set(fields[separator + 3].split(","))
            )
            records.append(
                {
                    "target": _unescape_mount(fields[4]),
                    "source": _unescape_mount(fields[separator + 2]),
                    "fstype": fields[separator + 1],
                    "options": options,
                    "fsroot": _unescape_mount(fields[3]),
                    "uuid": None,
                    "major_minor": fields[2],
                    "device_type": None,
                }
            )
    except (IndexError, ValueError) as exc:
        raise StorageError("storage_probe_failed") from exc
    if len(records) > 4096:
        raise StorageError("storage_probe_failed")
    return records


def _device_kind(path: str, pkname: str | None = None) -> str:
    names = f"{path} {pkname or ''}"
    if re.search(r"(?:^|/)nvme[0-9]+n[0-9]+", names):
        return "nvme"
    if re.search(r"(?:^|/)mmcblk[0-9]+", names):
        return "mmc"
    return "other"


def _read_fake_inventory(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mountinfo = root / "proc/self/mountinfo"
    try:
        info = mountinfo.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_size > _MAX_MOUNTINFO_BYTES
        ):
            raise OSError("unsafe mountinfo")
        raw = mountinfo.read_bytes()
        mounts = parse_mountinfo(raw.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise StorageError("storage_probe_failed") from exc

    uuid_by_path: dict[str, str] = {}
    uuid_root = root / "dev/disk/by-uuid"
    try:
        entries = list(uuid_root.iterdir()) if uuid_root.exists() else []
        if len(entries) > 256:
            raise OSError("too many UUID entries")
        logical_dev_root = (root / "dev").resolve()
        for entry in entries:
            if not entry.is_symlink() or not _UUID_RE.fullmatch(entry.name):
                raise OSError("unsafe UUID entry")
            target = entry.resolve(strict=True)
            if target.parent != logical_dev_root:
                raise OSError("unsafe UUID target")
            uuid_by_path[f"/dev/{target.name}"] = entry.name
    except OSError as exc:
        raise StorageError("storage_probe_failed") from exc

    sources = sorted(
        {row["source"] for row in mounts if row["source"].startswith("/dev/")}
        | set(uuid_by_path)
    )
    devices: list[dict[str, Any]] = []
    for source in sources:
        name = Path(source).name
        nvme = re.fullmatch(r"(nvme[0-9]+n[0-9]+)p[0-9]+", name)
        mmc = re.fullmatch(r"(mmcblk[0-9]+)p[0-9]+", name)
        pkname = (nvme or mmc).group(1) if nvme or mmc else None
        devices.append(
            {
                "name": name,
                "path": source,
                "type": "part" if pkname else "disk",
                "pkname": pkname,
                "uuid": uuid_by_path.get(source),
                "fstype": None,
                "size": None,
                "ro": False,
                "mountpoints": [
                    row["target"] for row in mounts if row["source"] == source
                ],
            }
        )
    return mounts, devices


def _run_json(argv: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
        )
        if (
            result.returncode != 0
            or len(result.stdout.encode("utf-8")) > _MAX_COMMAND_BYTES
        ):
            raise ValueError("probe command failed")
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise TypeError("invalid probe output")
        return value
    except (
        OSError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise StorageError("storage_probe_failed") from exc


def _flatten_lsblk(rows: Iterable[Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    pending = list(rows)
    while pending:
        raw = pending.pop(0)
        if not isinstance(raw, dict) or len(flattened) >= 512:
            raise StorageError("storage_probe_failed")
        children = raw.get("children", [])
        if children is not None:
            if not isinstance(children, list):
                raise StorageError("storage_probe_failed")
            pending[0:0] = children
        path = raw.get("path")
        name = raw.get("name")
        if (
            not isinstance(path, str)
            or not path.startswith("/dev/")
            or not isinstance(name, str)
        ):
            raise StorageError("storage_probe_failed")
        mountpoints = raw.get("mountpoints") or []
        if not isinstance(mountpoints, list):
            raise StorageError("storage_probe_failed")
        flattened.append(
            {
                "name": name,
                "path": path,
                "type": raw.get("type"),
                "pkname": raw.get("pkname"),
                "uuid": raw.get("uuid"),
                "fstype": raw.get("fstype"),
                "size": raw.get("size"),
                "ro": raw.get("ro") in (True, 1, "1"),
                "mountpoints": [
                    point for point in mountpoints if isinstance(point, str)
                ],
            }
        )
    return flattened


def _read_real_inventory() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    block_value = _run_json(
        [
            "lsblk",
            "--json",
            "--bytes",
            "--output",
            "NAME,PATH,TYPE,PKNAME,UUID,FSTYPE,SIZE,RO,MOUNTPOINTS",
        ]
    )
    devices = _flatten_lsblk(block_value.get("blockdevices", []))
    find_value = _run_json(
        [
            "findmnt",
            # Inspect the host, not ProtectSystem's intentionally read-only
            # service namespace. Host operations run natively alongside PID 1.
            "--task",
            "1",
            "--json",
            "--list",
            "--bytes",
            "--output",
            "TARGET,SOURCE,FSTYPE,OPTIONS,FSROOT,UUID,MAJ:MIN",
        ]
    )
    filesystems = find_value.get("filesystems")
    if not isinstance(filesystems, list) or len(filesystems) > 4096:
        raise StorageError("storage_probe_failed")
    mounts: list[dict[str, Any]] = []
    for raw in filesystems:
        if not isinstance(raw, dict):
            raise StorageError("storage_probe_failed")
        target, source = raw.get("target"), raw.get("source")
        if not isinstance(target, str) or not isinstance(source, str):
            raise StorageError("storage_probe_failed")
        source = re.sub(r"\[.*\]\Z", "", source)
        options = raw.get("options", "")
        mounts.append(
            {
                "target": target,
                "source": source,
                "fstype": raw.get("fstype"),
                "options": sorted(set(options.split(",")))
                if isinstance(options, str)
                else [],
                "fsroot": raw.get("fsroot") or "/",
                "uuid": raw.get("uuid"),
                "major_minor": raw.get("maj:min") or raw.get("maj_min"),
                "device_type": None,
            }
        )
    return mounts, devices


def inspect_storage(root: Path = Path("/")) -> dict[str, Any]:
    """Return a bounded, read-only mount and block-device inventory."""

    root = Path(root)
    mounts, devices = (
        _read_real_inventory() if root == Path("/") else _read_fake_inventory(root)
    )
    by_path = {row["path"]: row for row in devices}
    for mount in mounts:
        device = by_path.get(mount["source"])
        if device:
            mount["uuid"] = mount["uuid"] or device["uuid"]
            mount["device_type"] = _device_kind(device["path"], device["pkname"])
    root_mount = next((row for row in mounts if row["target"] == "/"), None)
    return {"root_mount": root_mount, "mounts": mounts, "block_devices": devices}


def _exact_mount(inventory: dict[str, Any], target: str) -> dict[str, Any]:
    matches = [row for row in inventory["mounts"] if row["target"] == target]
    if len(matches) != 1:
        raise StorageError("storage_mount_missing")
    return matches[0]


def _mount_for(inventory: dict[str, Any], target: str) -> dict[str, Any]:
    candidates = [
        row
        for row in inventory["mounts"]
        if row["target"] == "/"
        or target == row["target"]
        or target.startswith(row["target"].rstrip("/") + "/")
    ]
    if not candidates:
        raise StorageError("storage_mount_missing")
    return max(candidates, key=lambda row: len(row["target"]))


def _resolved_target(root: Path, logical: str) -> str:
    root_resolved = root.resolve()
    candidate = root / logical.lstrip("/")
    missing: list[str] = []
    while not candidate.exists() and not candidate.is_symlink() and candidate != root:
        missing.append(candidate.name)
        candidate = candidate.parent
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise StorageError("storage_path_mismatch") from exc
    for name in reversed(missing):
        resolved = resolved / name
    try:
        relative = resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise StorageError("storage_path_mismatch") from exc
    return "/" if relative == Path(".") else "/" + relative.as_posix()


def _ensure_rw(mount: dict[str, Any], inventory: dict[str, Any]) -> None:
    if "rw" not in mount["options"] or "ro" in mount["options"]:
        raise StorageError("storage_read_only")
    device = next(
        (row for row in inventory["block_devices"] if row["path"] == mount["source"]),
        None,
    )
    if device and device["ro"]:
        raise StorageError("storage_read_only")


def _check_uuid(mount: dict[str, Any], expected: str, code: str) -> None:
    if mount.get("uuid") != expected:
        raise StorageError(code)


def require_storage(
    root: Path = Path("/"),
    *,
    require_layout: bool = False,
    allow_preparing: bool = False,
    check_space: bool = True,
) -> dict[str, Any]:
    """Require the installed layout to match the live storage topology."""

    root = Path(root)
    layout = load_layout(root)
    if layout is None:
        if require_layout:
            raise StorageError("layout_missing")
        return {"state": "unmanaged"}
    if layout["state"] == "preparing" and not allow_preparing:
        raise StorageError("storage_preparing")
    inventory = inspect_storage(root)
    root_mount = inventory["root_mount"]
    if root_mount is None:
        raise StorageError("storage_mount_missing")
    _check_uuid(root_mount, layout["root_uuid"], "storage_root_uuid_mismatch")
    _ensure_rw(root_mount, inventory)

    if layout["mode"] == "nvme-root":
        if root_mount["source"] != layout["device"]:
            raise StorageError("storage_source_mismatch")
        if root_mount["fstype"] != "ext4":
            raise StorageError("storage_source_mismatch")
        if root_mount["device_type"] != "nvme":
            raise StorageError("storage_not_nvme")
        _check_uuid(root_mount, layout["uuid"], "storage_uuid_mismatch")
        for target in TARGETS.values():
            resolved = _resolved_target(root, target)
            mount = _mount_for(inventory, resolved)
            if (
                resolved != target
                or mount["source"] != layout["device"]
                or mount["fstype"] != "ext4"
                or str(
                    PurePosixPath(mount["fsroot"])
                    / PurePosixPath(target).relative_to(mount["target"])
                )
                != target
            ):
                raise StorageError("storage_path_mismatch")
            _check_uuid(mount, layout["uuid"], "storage_path_mismatch")
            _ensure_rw(mount, inventory)
        space_path = root
    else:
        if root_mount["device_type"] != "mmc":
            raise StorageError("storage_root_not_emmc")
        storage_mount = _exact_mount(inventory, STORAGE_ROOT)
        if (
            storage_mount["source"] != layout["device"]
            or storage_mount["fsroot"] != "/"
            or storage_mount["fstype"] != "ext4"
        ):
            raise StorageError("storage_source_mismatch")
        if storage_mount["device_type"] != "nvme":
            raise StorageError("storage_not_nvme")
        _check_uuid(storage_mount, layout["uuid"], "storage_uuid_mismatch")
        _ensure_rw(storage_mount, inventory)
        for source_name, target in TARGETS.items():
            mount = _exact_mount(inventory, target)
            if (
                mount["source"] != layout["device"]
                or mount["fsroot"] != f"/{source_name}"
                or mount["uuid"] != layout["uuid"]
            ):
                raise StorageError("storage_source_mismatch")
            _ensure_rw(mount, inventory)
            if _resolved_target(root, target) != target:
                raise StorageError("storage_path_mismatch")
        space_path = root / STORAGE_ROOT.lstrip("/")

    if check_space:
        try:
            space = os.statvfs(space_path)
        except OSError as exc:
            raise StorageError("storage_probe_failed") from exc
        if (
            space.f_bavail * space.f_frsize < layout["min_free_bytes"]
            or space.f_favail <= 0
        ):
            raise StorageError("storage_space_low")
    return {
        "state": layout["state"],
        "mode": layout["mode"],
        "uuid": layout["uuid"],
        "root_uuid": layout["root_uuid"],
        "device": layout["device"],
        "targets": list(TARGETS.values()),
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="robopark-storage-layout")
    parser.add_argument("command", choices=("check", "inspect"))
    parser.add_argument("--root", default="/")
    parser.add_argument(
        "--presence-only",
        action="store_true",
        help="Validate mounts without blocking low-space recovery",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    if root != Path("/") and os.environ.get("ROBOPARK_TESTING") != "1":
        print(
            json.dumps({"error": "test_root_forbidden", "safe": False}, sort_keys=True)
        )
        return 2
    try:
        result = (
            inspect_storage(root)
            if args.command == "inspect"
            else require_storage(root, check_space=not args.presence_only)
        )
        if args.command == "check" and result.get("state") != "unmanaged":
            require_container_configuration(root)
    except StorageError as exc:
        print(json.dumps({"error": exc.code, "safe": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
