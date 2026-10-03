"""Bounded read-only disk inventory for the owner's System screen.

Directory figures are allocated bytes. Docker's figures come from its own
accounting and can share layers, so the categories must not be summed.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_ENTRIES = 50_000
MAX_SCAN_SECONDS = 3.0
MAX_DOCKER_OUTPUT = 64 * 1024
MAX_BUILDER_OUTPUT = 2 * 1024 * 1024
MAX_BUILD_RECORDS = 1024
DOCKER_TYPES = {
    "Images": "docker_images",
    "Build Cache": "buildkit_cache",
    "Local Volumes": "docker_volumes",
}
POSTGRES_VOLUME = "robopark_robopark_postgres"
SIZE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?i?B)", re.IGNORECASE)


def _size_bytes(value: object) -> int | None:
    if type(value) is int:
        return value if 0 <= value < 2**63 else None
    if not isinstance(value, str) or (match := SIZE.fullmatch(value.strip())) is None:
        return None
    unit = match.group(2).lower()
    prefix = unit[0] if len(unit) > 1 else ""
    exponent = "kmgtpe".find(prefix) + 1 if prefix else 0
    try:
        number = Decimal(match.group(1)) * (1024 if "i" in unit else 1000) ** exponent
        result = int(number)
    except (InvalidOperation, OverflowError, ValueError):
        return None
    return result if 0 <= result < 2**63 else None


def parse_docker_df(output: str) -> dict[str, int] | None:
    """Accept Docker's JSON lines or one JSON array, never a human table."""
    if len(output) > MAX_DOCKER_OUTPUT:
        return None
    try:
        parsed = json.loads(output)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        try:
            rows = [json.loads(line) for line in output.splitlines() if line.strip()]
        except json.JSONDecodeError:
            return None
    result: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        category = DOCKER_TYPES.get(row.get("Type"))
        if category is None:
            continue
        size = _size_bytes(row.get("Size"))
        if size is None:
            return None
        result[category] = result.get(category, 0) + size
    return result if set(result) == set(DOCKER_TYPES.values()) else None


def parse_builder_size(size: object) -> int | None:
    """Accept Buildx byte counts and its bounded human-readable JSON sizes."""
    amount = (
        int(size)
        if isinstance(size, str) and re.fullmatch(r"[0-9]{1,19}", size)
        else _size_bytes(size)
    )
    return amount if amount is not None and 0 <= amount < 2**63 else None


def parse_owned_builder_du(output: str) -> dict[str, int] | None:
    """Bound a Buildx JSON-lines report; sizes are reported, not freed bytes."""
    if len(output) > MAX_BUILDER_OUTPUT:
        return None
    reported = 0
    private_reclaimable = 0
    seen: set[str] = set()
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) > MAX_BUILD_RECORDS:
        return None
    for line in lines:
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            return None
        if not isinstance(row, dict):
            return None
        identity = row.get("ID")
        size = row.get("Size")
        if (
            not isinstance(identity, str)
            or re.fullmatch(r"[a-z0-9]{1,64}", identity) is None
            or identity in seen
            or type(row.get("Reclaimable")) is not bool
            or type(row.get("Shared")) is not bool
            or type(size) not in (int, str)
        ):
            return None
        amount = parse_builder_size(size)
        if amount is None or reported + amount >= 2**63:
            return None
        seen.add(identity)
        reported += amount
        if row["Reclaimable"] and not row["Shared"]:
            private_reclaimable += amount
    return {
        "reported_bytes": reported,
        "private_reclaimable_bytes": private_reclaimable,
    }


def _postgres_mountpoint(output: str) -> Path | None:
    if len(output) > MAX_DOCKER_OUTPUT:
        return None
    try:
        values = json.loads(output)
        if not isinstance(values, list) or len(values) != 1:
            return None
        volume = values[0]
        labels = volume["Labels"]
        raw_path = volume["Mountpoint"]
        if (
            volume["Name"] != POSTGRES_VOLUME
            or volume["Driver"] != "local"
            or not isinstance(labels, dict)
            or labels.get("com.docker.compose.project") != "robopark"
            or labels.get("com.docker.compose.volume") != "robopark_postgres"
            or not isinstance(raw_path, str)
        ):
            return None
        path = Path(raw_path)
        if (
            not path.is_absolute()
            or ".." in path.parts
            or path.name != "_data"
            or path.parent.name != POSTGRES_VOLUME
            or path.resolve(strict=True) != path
        ):
            return None
        return path
    except (ValueError, KeyError, TypeError, OSError):
        return None


def _directory_bytes(root: Path) -> int | None:
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return 0
    except OSError:
        return None
    if not stat.S_ISDIR(root_info.st_mode):
        return None
    stack = [root]
    seen_inodes: set[tuple[int, int]] = {(root_info.st_dev, root_info.st_ino)}
    seen = 0
    total = root_info.st_blocks * 512
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    try:
        while stack:
            if time.monotonic() > deadline:
                return None
            directory = stack.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    seen += 1
                    if seen > MAX_ENTRIES or time.monotonic() > deadline:
                        return None
                    info = entry.stat(follow_symlinks=False)
                    if info.st_dev != root_info.st_dev or stat.S_ISLNK(info.st_mode):
                        return None
                    identity = (info.st_dev, info.st_ino)
                    if identity in seen_inodes:
                        continue
                    seen_inodes.add(identity)
                    if stat.S_ISDIR(info.st_mode):
                        total += info.st_blocks * 512
                        stack.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode):
                        total += info.st_blocks * 512
                    else:
                        return None
    except OSError:
        return None
    return total


def collect_storage_inventory(paths, *, runner=None) -> dict[str, object]:
    """Read exact Robopark roots and Docker's daemon-wide summary; no deletion."""
    roots = {
        "ota_uploads": paths.ops / "ota-uploads",
        "ota_cache": paths.state / "ota-packages",
        "releases": paths.releases,
        "backups": paths.var / "backups",
        "scheduled_backups": paths.root / "var/backups/robopark",
        "diagnostics": paths.var / "diagnostics",
        "logs": paths.root / "var/log/robopark",
    }
    categories: dict[str, int | None] = {
        key: _directory_bytes(root) for key, root in roots.items()
    }
    journals = [
        _directory_bytes(paths.root / "var/log/journal"),
        _directory_bytes(paths.root / "run/log/journal"),
    ]
    categories["journald"] = (
        sum(journals) if all(value is not None for value in journals) else None
    )
    categories.update({key: None for key in DOCKER_TYPES.values()})
    categories["robopark_buildkit_reported"] = None
    categories["robopark_buildkit_private_reclaimable"] = None
    categories["postgresql_data"] = None
    if runner is None and paths.root != Path("/"):
        return {"sampled_at": time.time(), "category_bytes": categories}
    run = runner or (
        lambda argv, *, timeout: subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            # Buildx stores builder definitions here, not in root's default
            # Docker profile. Never inherit a remote Docker context for probes.
            env={
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "HOME": str(paths.ops),
                "LANG": "C.UTF-8",
                "DOCKER_CONFIG": str(paths.ops / "docker-config"),
                "DOCKER_HOST": "unix:///var/run/docker.sock",
            },
        )
    )
    try:
        output = run(
            [
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "system",
                "df",
                "--format",
                "json",
            ],
            timeout=8,
        )
        if output.returncode == 0 and len(output.stdout) <= MAX_DOCKER_OUTPUT:
            parsed = parse_docker_df(output.stdout)
            if parsed is not None:
                categories.update(parsed)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        from .owned_builder import inspect_owned_builder

        deadline = time.monotonic() + 8

        def owned_run(argv):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("builder_inventory_timeout")
            command = ["docker", "--host", "unix:///var/run/docker.sock", *argv[1:]]
            output = run(command, timeout=min(8, remaining))
            maximum = MAX_BUILDER_OUTPUT if argv[:3] == ["docker", "buildx", "du"] else MAX_DOCKER_OUTPUT
            if output.returncode != 0 or len(output.stdout) > maximum:
                raise ValueError("builder_inventory_unavailable")
            return output.stdout

        builder = inspect_owned_builder(paths, owned_run)
        measured = parse_owned_builder_du(owned_run([
            "docker", "buildx", "du", "--builder", builder, "--format=json",
        ]))
        if measured is not None:
            categories["robopark_buildkit_reported"] = measured["reported_bytes"]
            categories["robopark_buildkit_private_reclaimable"] = measured[
                "private_reclaimable_bytes"
            ]
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
        pass
    try:
        inspected = run(
            [
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                "volume",
                "inspect",
                POSTGRES_VOLUME,
            ],
            timeout=8,
        )
        if inspected.returncode == 0 and len(inspected.stdout) <= MAX_DOCKER_OUTPUT:
            mountpoint = _postgres_mountpoint(inspected.stdout)
            if mountpoint is not None:
                categories["postgresql_data"] = _directory_bytes(mountpoint)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"sampled_at": time.time(), "category_bytes": categories}
