"""Crash-safe filesystem operations and rollback while writers are stopped."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

from .release import ReleaseError, verify_directory
from .terminal_install import TERMINAL_UNITS, quiesce_terminal

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


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_symlink(target, link):
    link.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".link-", dir=link.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        temporary.unlink()
        temporary.symlink_to(target)
        os.replace(temporary, link)
        sync_directory(link.parent)
    finally:
        temporary.unlink(missing_ok=True)


def restore_previous_link(paths, name):
    """A retired grandparent is no longer an available rollback target."""
    if name is not None:
        if not isinstance(name, str) or name in {"", ".", ".."} or Path(name).name != name:
            raise ReleaseError("unsafe_release_path")
        target = paths.releases / name
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ReleaseError("unsafe_release_path")
        if target.is_dir():
            atomic_symlink(target, paths.previous)
            return
    paths.previous.unlink(missing_ok=True)
    sync_directory(paths.opt)


def atomic_copy(source, target, mode=0o600):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".copy-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as dest, source.open("rb") as src:
            shutil.copyfileobj(src, dest)
            dest.flush()
            os.fchmod(dest.fileno(), mode)
            os.fsync(dest.fileno())
        os.replace(name, target)
        sync_directory(target.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def durable_copy_tree(source, destination):
    if source.is_symlink() or not source.is_dir():
        raise ReleaseError("unsafe_data_path")
    for path in source.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ReleaseError("unsafe_data_path")
    shutil.copytree(source, destination)
    for path in list(destination.rglob("*")) + [destination]:
        original = source / path.relative_to(destination)
        metadata = original.stat()
        current = path.stat()
        if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
            os.chown(path, metadata.st_uid, metadata.st_gid)
        path.chmod(metadata.st_mode & 0o777)
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in [p for p in destination.rglob("*") if p.is_dir()] + [
        destination,
        destination.parent,
    ]:
        sync_directory(path)


def _database_command(paths, arguments):
    from .updater import compose

    return compose("robopark", paths.state / "current-compose.json") + [
        "exec",
        "-T",
        "db",
        *arguments,
    ]


def verify_source_head(paths, runner, expected_head):
    """Read the live database head before any update journal or service change."""
    try:
        actual_head = runner.run(
            _database_command(
                paths,
                [
                    "psql",
                    "--username=robopark",
                    "--dbname=robopark",
                    "--tuples-only",
                    "--no-align",
                    "--command=SELECT version_num FROM alembic_version",
                ],
            ),
            timeout=30,
            capture=True,
        )
        if isinstance(actual_head, bytes):
            actual_head = actual_head.decode("utf-8", "strict")
        if actual_head.strip() != expected_head:
            raise ReleaseError("migration_head_mismatch")
    except Exception as error:
        raise ReleaseError("migration_head_mismatch") from error


def snapshot(paths, journal, runner=None, *, refresh=False):
    root = paths.ops / "rollbacks" / journal["job_id"]
    rollbacks_existed = root.parent.is_dir()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    if not rollbacks_existed:
        sync_directory(root.parent.parent)
    sync_directory(root.parent)
    temporary = root / "data.partial"
    if temporary.exists():
        shutil.rmtree(temporary)
    durable_copy_tree(paths.var / "data", temporary)
    preliminary = root / "data.preliminary"
    if refresh:
        if preliminary.exists():
            shutil.rmtree(preliminary)
        os.replace(root / "data", preliminary)
    os.replace(temporary, root / "data")
    sync_directory(root)
    if refresh:
        shutil.rmtree(preliminary)
    if runner is not None:
        current = paths.current.resolve(strict=True)
        expected_head = verify_directory(current, None)["migration_head"]
        verify_source_head(paths, runner, expected_head)
        target = f"/host-rollbacks/{journal['job_id']}/database.dump"
        runner.run(
            _database_command(
                paths,
                [
                    "pg_dump",
                    "--format=custom",
                    f"--file={target}",
                    "--username=robopark",
                    "--dbname=robopark",
                ],
            ),
            timeout=300,
        )
        runner.run(
            _database_command(paths, ["pg_restore", "--list", target]),
            timeout=60,
        )
        dump = root / "database.dump"
        try:
            descriptor = os.open(dump, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
                    raise ReleaseError("update_failed")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise ReleaseError("update_failed") from error
        sync_directory(root)
    backup = root / "units"
    backup.mkdir(mode=0o700, exist_ok=True)
    installed = paths.root / "etc/systemd/system"
    for unit in (*UNITS, *TERMINAL_UNITS):
        path = installed / unit
        if path.is_file():
            atomic_copy(path, backup / unit)
    sync_directory(backup)
    sync_directory(root)


def discard_rollback_artifacts(paths, journal):
    for target in (
        paths.ops / "rollbacks" / journal["job_id"],
        paths.var / (".displaced-" + journal["job_id"]),
    ):
        if target.is_symlink():
            raise ReleaseError("unsafe_path")
        if target.is_dir():
            shutil.rmtree(target)
            sync_directory(target.parent)


def restore_units(paths, journal):
    backup = paths.ops / "rollbacks" / journal["job_id"] / "units"
    installed = paths.root / "etc/systemd/system"
    for unit in (*UNITS, *TERMINAL_UNITS):
        path = installed / unit
        if (backup / unit).is_file():
            atomic_copy(backup / unit, path, 0o644)
        else:
            path.unlink(missing_ok=True)
    sync_directory(installed)


def restore_data(paths, journal, runner=None):
    root = paths.ops / "rollbacks" / journal["job_id"]
    temporary = paths.var / (".restore-" + journal["job_id"])
    saved = paths.var / (".displaced-" + journal["job_id"])
    if temporary.exists():
        shutil.rmtree(temporary)
    durable_copy_tree(root / "data", temporary)
    if (paths.var / "data").exists():
        if saved.exists():
            shutil.rmtree(saved)
        os.replace(paths.var / "data", saved)
        sync_directory(paths.var)
    os.replace(temporary, paths.var / "data")
    sync_directory(paths.var)
    database = root / "database.dump"
    if runner is not None and database.is_file():
        runner.run(
            _database_command(
                paths,
                [
                    "pg_restore",
                    "--clean",
                    "--if-exists",
                    "--create",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "--username=robopark",
                    # This is our private pg_dump of the fixed robopark DB.
                    # --clean alone leaves objects created by later migrations.
                    "--dbname=postgres",
                    f"/host-rollbacks/{journal['job_id']}/database.dump",
                ],
            ),
            timeout=600,
        )


def rollback_release(paths, journal, runner, phase):
    """Idempotent even if power is lost halfway through a snapshot restore."""
    previous = paths.releases / journal["previous"]
    verify_directory(previous, None)
    if journal["writes_resumed"]:
        raise ReleaseError("manual_recovery_required")
    quiesce_terminal(paths, runner, reason="rollback")
    phase("rolling_back")
    runner.run(["systemctl", "stop", "robopark.service"], timeout=120)
    if journal["migration_started"]:
        restore_data(paths, journal, runner)
    atomic_symlink(previous, paths.current)
    atomic_symlink(paths.state / journal["previous_config"], paths.state / "current-compose.json")
    restore_previous_link(paths, journal["original_previous"])
    if journal["snapshot_done"]:
        restore_units(paths, journal)
    atomic_symlink(previous / "deploy/host", paths.opt / "host-tools")
    runner.run(["systemctl", "daemon-reload"], timeout=60)
    runner.run(["systemctl", "restart", "robopark.service"], timeout=900)
    from .runtime import bot_enabled

    if not runner.wait_ready(
        project="robopark", config=paths.state / "current-compose.json", timeout=180,
        bot_required=bot_enabled(paths),
    ):
        raise ReleaseError("manual_recovery_required")
    from .terminal_install import reconcile_terminal_installation

    reconcile_terminal_installation(paths, previous, runner)
    phase("rollback_healthy")
