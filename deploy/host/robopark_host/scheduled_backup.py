"""Verified local snapshots for the active production Compose deployment."""

from __future__ import annotations

import os
import re
import stat
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .paths import HostPaths
from .state import atomic_write_json

ARTIFACT = re.compile(r"/ops/artifacts/snapshot-[a-f0-9-]{36}\.zip\Z")
LOCAL_BACKUP = re.compile(r"robopark-[0-9]{8}T[0-9]{12}\.zip\Z")
PARTIAL = re.compile(r"\.robopark-[a-z0-9_]{8}\.partial\Z")
MAX_LOCAL_BACKUP_BYTES = 4 * 1024**3
PARTIAL_TTL_SECONDS = 2 * 3600
PENDING_ACK = "scheduled-backup-pending-ack.json"


def _run(command: list[str], *, timeout: int = 600) -> str:
    from .updater import SystemRunner

    # SystemRunner bounds stdout/stderr, time and child processes. Its fresh
    # environment cannot inherit a remote Docker context from the shell.
    output = SystemRunner().run(
        command,
        timeout=timeout,
        capture=True,
        env={"DOCKER_HOST": "unix:///var/run/docker.sock"},
    )
    if len(output) > 4096:
        raise RuntimeError("backup_output_invalid")
    return output.decode("utf-8", "strict")


def _verify_snapshot(path: Path) -> None:
    import json

    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ValueError("backup_invalid")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("kind") != "snapshot":
                raise ValueError("backup_invalid")
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ValueError("backup_invalid") from exc


def _rotate_verified_backups(
    destination: Path, *, keep: int, max_bytes: int = MAX_LOCAL_BACKUP_BYTES
) -> None:
    """Limit owned local copies after a fresh copy has been durably confirmed."""
    if keep < 1 or max_bytes < 1:
        raise ValueError("backup_retention_invalid")
    device = destination.stat().st_dev
    owned: list[tuple[Path, int]] = []
    for item in destination.iterdir():
        if LOCAL_BACKUP.fullmatch(item.name) is None:
            continue
        info = item.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_dev == device:
            owned.append((item, info.st_blocks * 512))
    owned.sort(key=lambda entry: entry[0].name)
    total = sum(size for _, size in owned)
    remaining = len(owned)
    # Keep the newest two when policy permits. A single large copy can exceed
    # the soft byte budget; never discard the last known good recovery point.
    removed = False
    for item, size in owned[: -min(2, keep)]:
        if remaining <= keep and total <= max_bytes:
            break
        item.unlink()
        removed = True
        remaining -= 1
        total -= size
    if removed:
        directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _discard_abandoned_partials(destination: Path) -> None:
    """Reap only our unresumable copies after the service's 30-minute timeout."""
    device = destination.stat().st_dev
    cutoff = time.time() - PARTIAL_TTL_SECONDS
    for item in destination.iterdir():
        if PARTIAL.fullmatch(item.name) is None:
            continue
        info = item.lstat()
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_dev == device
            and info.st_mtime < cutoff
        ):
            item.unlink()


def _clear_pending_ack(path: Path) -> None:
    path.unlink()
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _retry_pending_ack(paths: HostPaths, destination: Path, compose: list[str], keep: int) -> None:
    from .operational_state import read_object

    pending = paths.state / PENDING_ACK
    if not pending.exists() and not pending.is_symlink():
        return
    value = read_object(pending, limit=4096)
    artifact = value.get("artifact")
    backup_name = value.get("backup")
    if (
        value.get("schema") != 1
        or not isinstance(artifact, str)
        or ARTIFACT.fullmatch(f"/ops/artifacts/{artifact}") is None
        or not isinstance(backup_name, str)
        or LOCAL_BACKUP.fullmatch(backup_name) is None
    ):
        raise ValueError("backup_ack_state_invalid")
    backup = destination / backup_name
    info = backup.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_dev != destination.stat().st_dev
    ):
        raise ValueError("backup_ack_state_invalid")
    _verify_snapshot(backup)
    _rotate_verified_backups(destination, keep=keep)
    _run([*compose, "exec", "-T", "api", "python", "-m", "robopark_api.services.ops.scheduled_snapshot", "--ack", artifact])
    _clear_pending_ack(pending)


def create_scheduled_backup(paths: HostPaths, *, keep: int = 14, parent_operation_id: str | None = None) -> Path:
    """Retain prior copies until a fresh snapshot is copied, verified and fsynced."""
    if not 1 <= keep <= 365:
        raise ValueError("backup_keep_invalid")
    if parent_operation_id is not None:
        from uuid import UUID
        if str(UUID(parent_operation_id)) != parent_operation_id:
            raise ValueError("invalid_parent_operation")
    destination = paths.root / "var/backups/robopark"
    if destination.is_symlink():
        raise ValueError("backup_directory_unsafe")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.stat().st_mode & 0o077:
        raise ValueError("backup_directory_unsafe")
    _discard_abandoned_partials(destination)
    compose = [
        "docker",
        "compose",
        "--project-name",
        "robopark",
        "--file",
        str(paths.state / "current-compose.json"),
    ]
    _retry_pending_ack(paths, destination, compose, keep)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    final = destination / f"robopark-{stamp}.zip"
    if final.exists() or final.is_symlink():
        raise ValueError("backup_name_collision")
    artifact = _run(
        [
            *compose,
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "robopark_api.services.ops.scheduled_snapshot",
            *(["--parent-operation", parent_operation_id] if parent_operation_id else []),
        ],
    ).strip()
    if ARTIFACT.fullmatch(artifact) is None:
        raise ValueError("backup_artifact_invalid")
    descriptor, name = tempfile.mkstemp(
        prefix=".robopark-", suffix=".partial", dir=destination
    )
    partial = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = -1
        _run([*compose, "cp", f"api:{artifact}", str(partial)])
        # Docker cp may replace the mkstemp inode or copy a permissive mode.
        # Reject anything except our private regular file before opening it.
        copy_fd = os.open(partial, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(copy_fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_dev != destination.stat().st_dev
                or info.st_uid != os.geteuid()
            ):
                raise ValueError("backup_copy_unsafe")
            os.fchmod(copy_fd, 0o600)
            os.fsync(copy_fd)
        finally:
            os.close(copy_fd)
        _verify_snapshot(partial)
        os.replace(partial, final)
        directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        # The verified local copy is already a recovery point. Rotate here so
        # a repeated failure to acknowledge the API artifact cannot grow disk.
        _rotate_verified_backups(destination, keep=keep)
        pending = paths.state / PENDING_ACK
        atomic_write_json(pending, {
            "schema": 1, "artifact": Path(artifact).name, "backup": final.name,
        })
        _run(
            [
                *compose,
                "exec",
                "-T",
                "api",
                "python",
                "-m",
                "robopark_api.services.ops.scheduled_snapshot",
                "--ack",
                Path(artifact).name,
            ]
        )
        _clear_pending_ack(pending)
        return final
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        partial.unlink(missing_ok=True)
