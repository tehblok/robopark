"""Read-only host and installer lifecycle detection.

This module deliberately does not import or execute code from an installed
release.  START.sh uses its fixed fields only to choose a mutation engine.
"""

from __future__ import annotations

import json
import os
import platform
import re
import stat
import sys
import zipfile
from pathlib import Path

VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
MAX_VERSION_BYTES = 256
MAX_OS_RELEASE_BYTES = 16 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 512 * 1024 * 1024


def _read_regular(path: Path, limit: int) -> bytes | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        value = os.read(descriptor, limit + 1)
        return value if len(value) <= limit else None
    finally:
        os.close(descriptor)


def _version(path: Path) -> str:
    raw = _read_regular(path, MAX_VERSION_BYTES)
    if raw is None:
        return "unknown"
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return "unknown"
    return value if VERSION_RE.fullmatch(value) else "unknown"


def _bundle_version(bundle: Path) -> str:
    payload = bundle / "payload/robopark-release.zip"
    raw = _read_regular(payload, MAX_PAYLOAD_BYTES)
    if raw is None:
        return "unknown"
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(raw)) as archive:
            info = archive.getinfo("manifest.json")
            if info.file_size > MAX_MANIFEST_BYTES:
                return "unknown"
            value = json.loads(archive.read(info))
        version = value.get("app_version") if isinstance(value, dict) else None
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return "unknown"
    return (
        version
        if isinstance(version, str) and VERSION_RE.fullmatch(version)
        else "unknown"
    )


def _parse_version(value: str):
    match = VERSION_RE.fullmatch(value)
    if not match:
        return None
    prerelease = match.group(4)
    parts = []
    if prerelease is not None:
        for item in prerelease.split("."):
            parts.append(int(item) if item.isdigit() else item)
    return (tuple(int(match.group(index)) for index in (1, 2, 3)), parts or None)


def _compare_prerelease(left, right) -> int:
    if left is None or right is None:
        return 0 if left is right else 1 if left is None else -1
    for left_item, right_item in zip(left, right):
        if left_item == right_item:
            continue
        if isinstance(left_item, int) and isinstance(right_item, str):
            return -1
        if isinstance(left_item, str) and isinstance(right_item, int):
            return 1
        return -1 if left_item < right_item else 1
    return (len(left) > len(right)) - (len(left) < len(right))


def version_relation(installed: str, bundled: str) -> str:
    if installed == "none":
        return "none"
    left, right = _parse_version(installed), _parse_version(bundled)
    if left is None or right is None:
        return "unknown"
    if left[0] != right[0]:
        comparison = -1 if left[0] < right[0] else 1
    else:
        comparison = _compare_prerelease(left[1], right[1])
    return "older" if comparison < 0 else "newer" if comparison > 0 else "same"


def _os_name(root: Path) -> str:
    raw = _read_regular(root / "etc/os-release", MAX_OS_RELEASE_BYTES)
    if raw is None:
        return "unknown"
    values = {}
    try:
        for line in raw.decode("utf-8").splitlines():
            if "=" not in line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    except UnicodeDecodeError:
        return "unknown"
    result = values.get("PRETTY_NAME") or " ".join(
        item for item in (values.get("ID", ""), values.get("VERSION_ID", "")) if item
    )
    return result or "unknown"


def _architecture(machine: str | None) -> str:
    value = machine or os.environ.get("ARCH") or platform.machine()
    return {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }.get(value, value or "unknown")


def _install_status(path: Path) -> str:
    raw = _read_regular(path, 64 * 1024)
    if raw is None:
        return "missing"
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return "invalid"
    if not isinstance(value, dict):
        return "invalid"
    return "complete" if value.get("status") == "complete" else "incomplete"


def _installed_identity(root: Path) -> tuple[str, bool, Path | None]:
    opt = root / "opt/robopark"
    releases = opt / "releases"
    current = opt / "current"
    if opt.is_symlink() or releases.is_symlink() or not current.is_symlink():
        return "unknown", False, None
    try:
        release = current.resolve(strict=True)
        release.relative_to(releases.resolve(strict=True))
    except (OSError, ValueError):
        return "unknown", False, None
    if not release.is_dir() or release.is_symlink():
        return "unknown", False, None
    return _version(release / "VERSION"), True, release


def detect(root: Path, bundle: Path, machine: str | None = None):
    root, bundle = Path(root), Path(bundle)
    opt = root / "opt/robopark"
    etc = root / "etc/robopark"
    var = root / "var/lib/robopark"
    installed, valid_current, release = _installed_identity(root)
    status = _install_status(var / "ops/state/install.json")
    host = opt / "host-tools/robopark"
    tools_match = False
    if valid_current and release is not None and (opt / "host-tools").is_symlink():
        try:
            tools_match = (opt / "host-tools").resolve(strict=True) == (
                release / "deploy/host"
            ).resolve(strict=True)
        except OSError:
            tools_match = False
    software_trace = os.path.lexists(opt) or os.path.lexists(etc)

    if status == "incomplete" and software_trace:
        state = "incomplete"
    elif (
        valid_current
        and installed != "unknown"
        and tools_match
        and host.is_file()
        and os.access(host, os.X_OK)
    ):
        state = "installed"
    elif software_trace:
        state = "damaged"
    elif os.path.lexists(var):
        state = "removed-data"
        installed = "none"
    else:
        state = "absent"
        installed = "none"

    bundled = _bundle_version(bundle)
    return {
        "state": state,
        "installed_version": installed,
        "bundle_version": bundled,
        "relation": version_relation(installed, bundled),
        "os": _os_name(root),
        "arch": _architecture(machine),
    }


def _clean(value: str) -> str:
    return " ".join(value.replace("=", "-").split())[:256] or "unknown"


def main(argv) -> int:
    if len(argv) != 3:
        print("usage: lifecycle.py ROOT BUNDLE", file=sys.stderr)
        return 2
    result = detect(Path(argv[1]), Path(argv[2]))
    for output, key in (
        ("STATE", "state"),
        ("INSTALLED_VERSION", "installed_version"),
        ("BUNDLE_VERSION", "bundle_version"),
        ("RELATION", "relation"),
        ("OS", "os"),
        ("ARCH", "arch"),
    ):
        print(f"{output}={_clean(result[key])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
