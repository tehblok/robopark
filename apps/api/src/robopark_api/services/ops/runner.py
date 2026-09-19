"""Orchestrate snapshot, restore, and tested release cutover."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from robopark_api.services.ops.archives import (
    KIND_RELEASE,
    KIND_SNAPSHOT,
    ArchiveError,
    build_archive,
    inspect_archive,
    unpack_archive,
)
from robopark_api.services.ops.jobs import (
    KIND_RESTORE as JOB_RESTORE,
)
from robopark_api.services.ops.jobs import (
    KIND_SNAPSHOT as JOB_SNAPSHOT,
)
from robopark_api.services.ops.jobs import (
    KIND_UPDATE as JOB_UPDATE,
)
from robopark_api.services.ops.jobs import (
    PHASE_AWAITING_REBUILD,
    STATE_FAILED,
    STATE_SUCCEEDED,
    JobAborted,
    JobConflict,
    OpsJob,
    append_log,
    begin_exclusive,
    ensure_ops_dir,
    host_has_job,
    load_job,
    publish_host_approval,
    save_job,
)
from robopark_api.services.ops.snapshot import (
    SnapshotError,
    build_snapshot_tree,
    restore_snapshot_tree,
)

RESTORE_PHRASE = "ВОССТАНОВИТЬ"
UPDATE_PHRASE = "ОБНОВИТЬ"

_SKIP_APPLY_PARTS = frozenset(
    {".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", ".git"}
)
_SECRET_BASENAMES = frozenset({"host.env", "tuna.env", ".env"})


class OpsError(ValueError):
    """User-facing ops failure with a stable detail token."""


class ReleaseTestsFailed(OpsError):
    def __init__(self, log: str):
        super().__init__("tests_failed")
        self.log = log


@dataclass
class OpsContext:
    ops_dir: Path
    database_url: str
    config_files: dict[str, Path] = field(default_factory=dict)
    data_dir: Path | None = None
    apply_root: Path | None = None
    app_version: str = "0.1.0"
    migration_head: str | None = None
    test_runner: Callable[[Path], str] | None = None
    health_check: Callable[[], bool] | None = None
    before_db_replace: Callable[[], None] | None = None
    use_ops_agent: bool | None = None
    release_public_key: bytes | None = None
    use_host_updater: bool = False
    actor_user_id: int | None = None
    host_ops_dir: Path | None = None


def begin_job(ctx: OpsContext, kind: str, *, exempt_token_hash: str) -> OpsJob:
    return begin_exclusive(ctx.ops_dir, kind, exempt_token_hash=exempt_token_hash)


def _require_host_writes(ctx: OpsContext) -> None:
    from robopark_api.services.ops.maintenance import HostMaintenanceActive, host_marker_active

    if ctx.use_host_updater and (ctx.host_ops_dir is None or host_marker_active(ctx.host_ops_dir)):
        raise HostMaintenanceActive()


def _aborted_job(ctx: OpsContext, job: OpsJob) -> OpsJob:
    disk = load_job(ctx.ops_dir)
    return disk if disk is not None else job


def fail_job(ctx: OpsContext, job: OpsJob, token: str, log: str = "") -> OpsJob:
    try:
        disk = load_job(ctx.ops_dir)
        if (
            disk is not None
            and disk.id == job.id
            and (disk.state == STATE_FAILED or host_has_job(disk))
        ):
            return disk
        if host_has_job(job):
            # Even cleanup errors after approval cannot release host ownership
            # or expose external exception details in the public API job log.
            return job
        if log:
            append_log(ctx.ops_dir, job, log)
        job.state = STATE_FAILED
        job.error = token
        job.phase = "failed"
        save_job(ctx.ops_dir, job)
        return job
    except JobAborted:
        return _aborted_job(ctx, job)


def succeed_job(ctx: OpsContext, job: OpsJob, *, phase: str = "done") -> OpsJob:
    try:
        job.state = STATE_SUCCEEDED
        job.phase = phase
        job.error = None
        save_job(ctx.ops_dir, job)
        return job
    except JobAborted:
        return _aborted_job(ctx, job)


def _run_tests(ctx: OpsContext, staging: Path) -> str:
    runner = ctx.test_runner or default_test_runner
    return runner(staging)


def default_test_runner(staging: Path) -> str:
    api_root = staging / "apps" / "api"
    tests_dir = api_root / "tests"
    if not tests_dir.is_dir():
        raise ReleaseTestsFailed("tests_missing")
    with tempfile.TemporaryDirectory(prefix="robopark-ops-pytest-") as tmp:
        env = os.environ.copy()
        env["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'pytest.db'}"
        env["PYTHONPATH"] = str((api_root / "src").resolve())
        env["DEV_SEED"] = "false"
        env.pop("SEED_PASSWORD", None)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "--tb=line", "-x"],
            cwd=api_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    log = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        raise ReleaseTestsFailed(log[-8000:] if log else "tests_failed")
    return log[-8000:]


def _should_skip_apply_path(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    if any(part in _SKIP_APPLY_PARTS for part in rel_parts):
        return True
    return path.name in _SECRET_BASENAMES


def _copy_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if _should_skip_apply_path(path, src):
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _rollback_apply(staging: Path, dest: Path, backup: Path) -> None:
    for path in staging.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        if _should_skip_apply_path(path, staging):
            continue
        rel = path.relative_to(staging)
        target = dest / rel
        saved = backup / rel
        if saved.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, target)
        elif target.exists():
            target.unlink()


def _write_artifact(ctx: OpsContext, job: OpsJob, data: bytes) -> Path:
    paths = ensure_ops_dir(ctx.ops_dir)
    name = f"{job.kind}-{job.id}.zip"
    dest = paths["artifacts"] / name
    dest.write_bytes(data)
    job.artifact_name = name
    save_job(ctx.ops_dir, job)
    return dest


def artifact_path(ops_dir: Path, job: OpsJob) -> Path | None:
    if not job.artifact_name:
        return None
    # Basename only — reject path traversal in a tampered job.json.
    name = Path(job.artifact_name).name
    if name != job.artifact_name or name in {"", ".", ".."}:
        return None
    root = ensure_ops_dir(ops_dir)["artifacts"].resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        return None
    return path if path.is_file() else None


def create_snapshot_bytes(ctx: OpsContext) -> bytes:
    paths = ensure_ops_dir(ctx.ops_dir)
    tree = paths["staging"] / "snapshot-tree"
    build_snapshot_tree(
        tree,
        database_url=ctx.database_url,
        expected_head=ctx.migration_head,
        config_files=ctx.config_files,
        data_dir=ctx.data_dir,
        skip_dirs=[paths["root"]],
    )
    return build_archive(kind=KIND_SNAPSHOT, source_root=tree, app_version=ctx.app_version)


def run_snapshot(ctx: OpsContext, job: OpsJob) -> OpsJob:
    try:
        job.phase = "building_snapshot"
        save_job(ctx.ops_dir, job)
        append_log(ctx.ops_dir, job, "Сборка снимка…")
        blob = create_snapshot_bytes(ctx)
        _write_artifact(ctx, job, blob)
        from robopark_api.services.ops.host_bridge import _atomic

        _atomic(
            ctx.ops_dir / "last-backup.json",
            json.dumps(
                {
                    "status": "success",
                    "completed_at": datetime.now(UTC).isoformat(),
                }
            ).encode(),
        )
        append_log(ctx.ops_dir, job, "Снимок готов.")
        return succeed_job(ctx, job, phase="ready")
    except JobAborted:
        return _aborted_job(ctx, job)
    except (SnapshotError, ArchiveError, OSError) as exc:
        token = str(exc) if isinstance(exc, SnapshotError | ArchiveError) else "snapshot_failed"
        return fail_job(ctx, job, token, log=str(exc))


def run_restore(ctx: OpsContext, job: OpsJob, archive: bytes, *, confirm: str) -> OpsJob:
    from robopark_api.services.ops.maintenance import HostMaintenanceActive

    if ctx.use_host_updater:
        return fail_job(ctx, job, "host_restore_required")
    if confirm.strip() != RESTORE_PHRASE:
        return fail_job(ctx, job, "confirm_required")
    try:
        job.phase = "validating"
        save_job(ctx.ops_dir, job)
        inspect_archive(archive, expected_kind=KIND_SNAPSHOT)
        paths = ensure_ops_dir(ctx.ops_dir)
        tree = paths["staging"] / "restore-tree"
        if tree.exists():
            shutil.rmtree(tree)
        save_job(ctx.ops_dir, job)
        unpack_archive(archive, tree, expected_kind=KIND_SNAPSHOT)
        job.phase = "replacing_data"
        save_job(ctx.ops_dir, job)
        append_log(ctx.ops_dir, job, "Восстановление данных…")
        if ctx.before_db_replace:
            ctx.before_db_replace()
        save_job(ctx.ops_dir, job)
        _require_host_writes(ctx)
        restore_snapshot_tree(
            tree,
            database_url=ctx.database_url,
            config_targets=ctx.config_files,
            data_dir=ctx.data_dir,
            preserve_dirs=[paths["root"]],
        )
        append_log(ctx.ops_dir, job, "Восстановление завершено.")
        job.restart_required = True
        return succeed_job(ctx, job, phase="restored")
    except JobAborted:
        return _aborted_job(ctx, job)
    except HostMaintenanceActive:
        return fail_job(ctx, job, "maintenance")
    except ArchiveError as exc:
        return fail_job(ctx, job, str(exc))
    except SnapshotError as exc:
        return fail_job(ctx, job, str(exc))
    except OSError as exc:
        return fail_job(ctx, job, "restore_failed", log=str(exc))


def _ops_agent_available(ctx: OpsContext) -> bool:
    if ctx.use_ops_agent is not None:
        return ctx.use_ops_agent
    return (Path("/host-repo") / "deploy" / "docker-compose.yml").is_file()


def _queue_host_update(ctx: OpsContext, job: OpsJob, archive: bytes) -> OpsJob:
    """Publish only approval and signed bytes; privileged work belongs to the host."""
    if type(ctx.actor_user_id) is not int or not 0 < ctx.actor_user_id < 2**63:
        return fail_job(ctx, job, "actor_required")
    paths = ensure_ops_dir(ctx.ops_dir)
    host_ops = ctx.host_ops_dir or paths["root"]
    artifacts = host_ops / "artifacts"
    artifacts.mkdir(parents=True, mode=0o700, exist_ok=True)
    artifact = artifacts / f"update-{job.id}.zip"
    descriptor, temporary = tempfile.mkstemp(prefix=".upload-", dir=artifacts)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(archive)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, artifact)
        directory = os.open(artifacts, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    request = {
        "job_id": job.id,
        "kind": "update",
        "artifact": artifact.name,
        "actor_user_id": ctx.actor_user_id,
        "created_at": job.created_at,
    }
    job.extra["host_updater"] = True
    job.extra["host_request"] = request
    inbox = host_ops / "inbox"
    inbox.mkdir(mode=0o700, exist_ok=True)
    job.phase = PHASE_AWAITING_REBUILD
    job.restart_required = True
    save_job(ctx.ops_dir, job)
    descriptor, name = tempfile.mkstemp(prefix=".approved-", dir=inbox)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(request, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Aborted jobs may never leave behind a new approval.
        save_job(ctx.ops_dir, job)
        publish_host_approval(ctx.ops_dir, job, Path(name), inbox / "approved.json")
    finally:
        Path(name).unlink(missing_ok=True)
    return job


def run_update(ctx: OpsContext, job: OpsJob, archive: bytes, *, confirm: str) -> OpsJob:
    if confirm.strip() != UPDATE_PHRASE:
        return fail_job(ctx, job, "confirm_required")
    paths = ensure_ops_dir(ctx.ops_dir)
    staging = paths["staging"] / "release"
    rollback_tree = paths["rollbacks"] / job.id
    try:
        job.phase = "validating"
        save_job(ctx.ops_dir, job)
        inspect_archive(archive, expected_kind=KIND_RELEASE, public_key=ctx.release_public_key)
        if ctx.use_host_updater:
            return _queue_host_update(ctx, job, archive)
        if staging.exists():
            shutil.rmtree(staging)
        save_job(ctx.ops_dir, job)
        unpack_archive(
            archive,
            staging,
            expected_kind=KIND_RELEASE,
            public_key=ctx.release_public_key,
        )
        append_log(ctx.ops_dir, job, "Архив проверен, запуск тестов…")
        job.phase = "testing"
        save_job(ctx.ops_dir, job)
        test_log = _run_tests(ctx, staging)
        if test_log:
            append_log(ctx.ops_dir, job, test_log)
        append_log(ctx.ops_dir, job, "Тесты прошли. Снимок перед выкладкой…")
        job.phase = "pre_cutover_snapshot"
        save_job(ctx.ops_dir, job)
        build_snapshot_tree(
            rollback_tree,
            database_url=ctx.database_url,
            expected_head=ctx.migration_head,
            config_files=ctx.config_files,
            data_dir=ctx.data_dir,
            skip_dirs=[paths["root"]],
        )
        if ctx.apply_root is None:
            return fail_job(ctx, job, "apply_root_missing")

        if _ops_agent_available(ctx):
            job.phase = PHASE_AWAITING_REBUILD
            job.restart_required = True
            save_job(ctx.ops_dir, job)
            paths["rebuild_result"].unlink(missing_ok=True)
            save_job(ctx.ops_dir, job)
            paths["rebuild_requested"].write_text(
                f"{job.id}\n{staging.resolve()}\n",
                encoding="utf-8",
            )
            append_log(
                ctx.ops_dir,
                job,
                "Staging готов. Ждём ops-agent (техработы остаются до перезапуска).",
            )
            # Stay running so maintenance continues across container rebuild.
            return job

        job.phase = "applying"
        save_job(ctx.ops_dir, job)
        append_log(ctx.ops_dir, job, "Копирование новой версии…")
        code_backup = paths["rollbacks"] / f"{job.id}-code"
        if ctx.apply_root.exists():
            _copy_tree(ctx.apply_root, code_backup)
        save_job(ctx.ops_dir, job)
        _copy_tree(staging, ctx.apply_root)
        check = ctx.health_check or (lambda: True)
        if not check():
            append_log(ctx.ops_dir, job, "Новая версия не поднялась, откат…")
            job.phase = "rolling_back"
            save_job(ctx.ops_dir, job)
            _rollback_apply(staging, ctx.apply_root, code_backup)
            if ctx.before_db_replace:
                ctx.before_db_replace()
            restore_snapshot_tree(
                rollback_tree,
                database_url=ctx.database_url,
                config_targets=ctx.config_files,
                data_dir=ctx.data_dir,
                preserve_dirs=[paths["root"]],
            )
            return fail_job(ctx, job, "cutover_unhealthy")
        job.restart_required = True
        append_log(ctx.ops_dir, job, "Выкладка завершена.")
        return succeed_job(ctx, job, phase="applied")
    except JobAborted:
        paths["rebuild_requested"].unlink(missing_ok=True)
        return _aborted_job(ctx, job)
    except ArchiveError as exc:
        return fail_job(ctx, job, str(exc))
    except ReleaseTestsFailed as exc:
        return fail_job(ctx, job, "tests_failed", log=exc.log)
    except (SnapshotError, OSError) as exc:
        token = str(exc) if isinstance(exc, SnapshotError) else "update_failed"
        return fail_job(ctx, job, token, log=str(exc))


def execute_job(
    ctx: OpsContext,
    job: OpsJob,
    *,
    archive: bytes | None = None,
    confirm: str = "",
) -> OpsJob:
    from robopark_api.services.ops.maintenance import HostMaintenanceActive

    try:
        # Host updates only publish an operational request; the root consumer
        # owns their writer barrier. Local snapshot/restore tasks touch data.
        if job.kind != JOB_UPDATE or not ctx.use_host_updater:
            _require_host_writes(ctx)
        if job.kind == JOB_SNAPSHOT:
            return run_snapshot(ctx, job)
        if job.kind == JOB_RESTORE:
            if archive is None:
                return fail_job(ctx, job, "archive_required")
            return run_restore(ctx, job, archive, confirm=confirm)
        if job.kind == JOB_UPDATE:
            if archive is None:
                return fail_job(ctx, job, "archive_required")
            return run_update(ctx, job, archive, confirm=confirm)
        return fail_job(ctx, job, "unknown_job")
    except JobAborted:
        return _aborted_job(ctx, job)
    except HostMaintenanceActive:
        return fail_job(ctx, job, "maintenance")


def start_and_run(
    ctx: OpsContext,
    kind: str,
    *,
    exempt_token_hash: str,
    archive: bytes | None = None,
    confirm: str = "",
) -> OpsJob:
    if kind == JOB_RESTORE and confirm.strip() != RESTORE_PHRASE:
        raise OpsError("confirm_required")
    if kind == JOB_UPDATE and confirm.strip() != UPDATE_PHRASE:
        raise OpsError("confirm_required")
    try:
        job = begin_job(ctx, kind, exempt_token_hash=exempt_token_hash)
    except JobConflict:
        raise
    return execute_job(ctx, job, archive=archive, confirm=confirm)


def current_job(ops_dir: Path) -> OpsJob | None:
    return load_job(ops_dir)
