"""Run inside the API container; stdout is only the validated artifact path."""

import json
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from uuid import UUID

from robopark_api.config import get_settings
from robopark_api.services.ops.archives import inspect_archive, write_archive
from robopark_api.services.ops.context import build_ops_context
from robopark_api.services.ops.jobs import ACTIVE_STATES, expire_stale_job, load_job
from robopark_api.services.ops.runner import (
    artifact_path,
    begin_job,
    execute_job,
    fail_job,
)
from robopark_api.services.ops.snapshot import (
    SNAPSHOT_DB_REL,
    SNAPSHOT_DUMP_REL,
    build_snapshot_tree,
)

_SCHEDULED_NAME = re.compile(r"snapshot-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.zip\Z")
_MAX_TRACKED = 8


def _tracked(ops_dir: Path) -> list[str]:
    index = ops_dir / "scheduled-artifacts.json"
    try:
        descriptor = os.open(index, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 8192
            ):
                raise RuntimeError("scheduled_index_invalid")
            names = json.loads(stream.read())
    except (OSError, ValueError, UnicodeError) as exc:
        raise RuntimeError("scheduled_index_invalid") from exc
    if (
        not isinstance(names, list)
        or len(names) > _MAX_TRACKED
        or any(
            not isinstance(name, str) or _SCHEDULED_NAME.fullmatch(name) is None for name in names
        )
        or len(names) != len(set(names))
    ):
        raise RuntimeError("scheduled_index_invalid")
    return names


def _save_tracked(ops_dir: Path, names: list[str]) -> None:
    if len(names) > _MAX_TRACKED:
        raise RuntimeError("scheduled_index_full")
    ops_dir.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".scheduled-artifacts-", dir=ops_dir)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(names, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, ops_dir / "scheduled-artifacts.json")
        directory = os.open(ops_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _discard_unverified_artifact(ops_dir: Path, name: str) -> None:
    """Remove only this run's unverified regular archive before freeing its slot."""
    if _SCHEDULED_NAME.fullmatch(name) is None:
        raise RuntimeError("scheduled_artifact_invalid")
    root = ops_dir / "artifacts"
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("scheduled_artifact_invalid")
    candidate = root / name
    info = candidate.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or info.st_dev != root.stat().st_dev
    ):
        raise RuntimeError("scheduled_artifact_invalid")
    candidate.unlink()
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    _save_tracked(ops_dir, [item for item in _tracked(ops_dir) if item != name])


def _parent_backup(ctx, operation_id):
    try:
        if str(UUID(operation_id)) != operation_id:
            raise ValueError()
        parent = load_job(ctx.ops_dir)
        request = (parent.extra or {}).get("host_request", {}) if parent else {}
        authorization = request.get("authorization", {})
        actor = request.get("actor_user_id")
        if not (
            parent is not None
            and parent.id == operation_id
            and parent.kind == "backup"
            and parent.state in ACTIVE_STATES
            and (parent.extra or {}).get("host_updater") is True
            and request.get("job_id") == operation_id
            and request.get("kind") == "backup"
            and type(actor) is int
            and actor > 0
            and authorization.get("consumed") is True
            and authorization.get("operation_id") == operation_id
            and authorization.get("operation_kind") == "backup"
            and authorization.get("actor_user_id") == actor
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("parent_backup_required") from None
    return parent


def _parent_snapshot(ctx, operation_id):
    _parent_backup(ctx, operation_id)
    name = f"snapshot-{operation_id}.zip"
    names = _tracked(ctx.ops_dir)
    root = ctx.ops_dir / "artifacts"
    if root.is_symlink():
        raise RuntimeError("scheduled_artifact_invalid")
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (
            name not in names
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
        ):
            raise RuntimeError("scheduled_artifact_invalid")
        return path.resolve(), name
    if name not in names:
        _save_tracked(ctx.ops_dir, [*names, name])
    descriptor, temporary_name = tempfile.mkstemp(prefix=".parent-snapshot-", dir=root)
    temporary = Path(temporary_name)
    try:
        with (
            os.fdopen(descriptor, "w+b") as output,
            tempfile.TemporaryDirectory(prefix="parent-snapshot-", dir=ctx.ops_dir) as folder,
        ):
            tree = Path(folder) / "tree"
            build_snapshot_tree(
                tree,
                database_url=ctx.database_url,
                expected_head=ctx.migration_head,
                config_files=ctx.config_files,
                data_dir=ctx.data_dir,
                skip_dirs=[ctx.ops_dir],
            )
            write_archive(
                kind="snapshot", source_root=tree, app_version=ctx.app_version, destination=output
            )
            output.flush()
            os.fsync(output.fileno())
        # Never overwrite a different file, even after interrupted publication.
        os.link(temporary, path)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return path.resolve(), name


def main():
    ctx = build_ops_context(get_settings())
    if len(sys.argv) == 3 and sys.argv[1] == "--ack":
        name = sys.argv[2]
        names = _tracked(ctx.ops_dir)
        if _SCHEDULED_NAME.fullmatch(name) is None or name not in names:
            raise RuntimeError("invalid_artifact")
        root = ctx.ops_dir / "artifacts"
        artifact = root / name
        if artifact.is_symlink() or not artifact.is_file():
            raise RuntimeError("artifact_missing")
        active = load_job(ctx.ops_dir)
        if active is not None and active.kind == "backup" and name == f"snapshot-{active.id}.zip":
            _parent_backup(ctx, active.id)
            _discard_unverified_artifact(ctx.ops_dir, name)
            return
        receipt = ctx.ops_dir / "scheduled-copy.json"
        try:
            previous = json.loads(receipt.read_text())
        except (OSError, ValueError):
            previous = {}
        verified_at = (
            previous.get("verified_at")
            if isinstance(previous, dict)
            and previous.get("artifact") == name
            and isinstance(previous.get("verified_at"), int | float)
            and math.isfinite(previous["verified_at"])
            and 0 < previous["verified_at"] <= time.time()
            else time.time()
        )
        receipt_pending = receipt.with_suffix(".tmp")
        with receipt_pending.open("w") as stream:
            json.dump({"artifact": name, "verified_at": verified_at}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        receipt_pending.replace(receipt)
        directory = os.open(ctx.ops_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        for old in names:
            if old != name:
                (root / old).unlink(missing_ok=True)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        _save_tracked(ctx.ops_dir, [name])
        return
    if len(sys.argv) == 3 and sys.argv[1] == "--parent-operation":
        path, reserved_name = _parent_snapshot(ctx, sys.argv[2])
    elif len(sys.argv) != 1:
        raise RuntimeError("invalid_snapshot_arguments")
    else:
        names = _tracked(ctx.ops_dir)
        active = load_job(ctx.ops_dir)
        if (
            active is not None
            and active.state in ACTIVE_STATES
            and active.kind == "snapshot"
            and active.exempt_token_hash == "scheduled-backup"
        ):
            # The host service has a 30-minute timeout. A later run may release
            # only its own abandoned job after the normal ops TTL has elapsed.
            expire_stale_job(ctx.ops_dir, expected_id=active.id)
            active = load_job(ctx.ops_dir)
        if active is None or active.state not in ACTIVE_STATES:
            root = ctx.ops_dir / "artifacts"
            if root.is_symlink():
                raise RuntimeError("scheduled_artifact_invalid")
            # An interrupted unlink can leave an index reservation without a file.
            # Keep every existing artifact and every reservation of an active job.
            existing = [
                name for name in names if (root / name).exists() or (root / name).is_symlink()
            ]
            if len(existing) != len(names):
                _save_tracked(ctx.ops_dir, existing)
                names = existing
        if len(names) >= _MAX_TRACKED:
            raise RuntimeError("scheduled_index_full")
        job = begin_job(ctx, "snapshot", exempt_token_hash="scheduled-backup")
        reserved_name = f"snapshot-{job.id}.zip"
        try:
            _save_tracked(ctx.ops_dir, [*names, reserved_name])
        except (OSError, RuntimeError):
            fail_job(ctx, job, "scheduled_index_failed")
            raise
        job = execute_job(ctx, job)
        path = artifact_path(ctx.ops_dir, job)
        if job.state != "succeeded" or path is None:
            candidate = ctx.ops_dir / "artifacts" / reserved_name
            if (
                job.state not in ACTIVE_STATES
                and path is None
                and not candidate.exists()
                and not candidate.is_symlink()
            ):
                _save_tracked(
                    ctx.ops_dir, [name for name in _tracked(ctx.ops_dir) if name != reserved_name]
                )
            raise RuntimeError("snapshot_failed")
    try:
        if path != (ctx.ops_dir / "artifacts" / reserved_name).resolve():
            raise RuntimeError("scheduled_artifact_invalid")
        with path.open("rb") as stream:
            inspect_archive(stream)
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as folder:
            if str(SNAPSHOT_DUMP_REL) in archive.namelist():
                database = Path(folder) / "check.dump"
                with archive.open(str(SNAPSHOT_DUMP_REL)) as source, database.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                result = subprocess.run(
                    ["pg_restore", "--list", str(database)],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode or "alembic_version" not in result.stdout:
                    raise RuntimeError("snapshot_database_invalid")
            else:
                database = Path(folder) / "check.db"
                with archive.open(str(SNAPSHOT_DB_REL)) as source, database.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
                    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise RuntimeError("snapshot_database_invalid")
    except Exception:
        _discard_unverified_artifact(ctx.ops_dir, reserved_name)
        raise
    print(path)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("scheduled_snapshot_failed", file=sys.stderr)
        sys.exit(1)
