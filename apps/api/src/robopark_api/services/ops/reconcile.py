"""Finalize ops jobs left awaiting Docker rebuild across API restarts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from robopark_api.services.ops.jobs import (
    PHASE_AWAITING_REBUILD,
    STATE_FAILED,
    STATE_RUNNING,
    STATE_SUCCEEDED,
    append_log,
    ensure_ops_dir,
    load_job,
    save_job,
)
from robopark_api.services.ops.snapshot import restore_snapshot_tree, sqlite_path_from_url

logger = logging.getLogger(__name__)


def reconcile_pending_rebuild(
    ops_dir: Path,
    *,
    database_url: str,
    config_files: dict[str, Path],
    data_dir: Path | None,
    host_ops_dir: Path | None = None,
) -> None:
    paths = ensure_ops_dir(ops_dir)
    job = load_job(ops_dir)
    if job is None:
        return
    if job.state != STATE_RUNNING or job.phase != PHASE_AWAITING_REBUILD:
        return

    host_updater = job.extra.get("host_updater") is True
    result_path = (
        (host_ops_dir or paths["root"]) / "public/rebuild.result"
        if host_updater
        else paths["rebuild_result"]
    )
    if not result_path.is_file():
        # Agent not finished yet — keep maintenance.
        return

    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Invalid rebuild.result: %s", exc)
        return

    if not host_updater:
        result_path.unlink(missing_ok=True)
        paths["rebuild_requested"].unlink(missing_ok=True)

    job_id = str(payload.get("job_id") or "")
    if job_id and job_id != job.id:
        logger.warning("rebuild.result job_id mismatch: %s vs %s", job_id, job.id)
        return

    if payload.get("ok"):
        append_log(
            ops_dir,
            job,
            "Host updater: обновление завершено."
            if host_updater
            else "ops-agent: контейнеры пересобраны.",
        )
        job.state = STATE_SUCCEEDED
        job.phase = "applied"
        job.error = None
        job.restart_required = False
        save_job(ops_dir, job)
        return

    error = str(payload.get("error") or "cutover_unhealthy")
    append_log(
        ops_dir,
        job,
        f"Host updater: обновление не выполнено ({error})."
        if host_updater
        else f"ops-agent: сбой выкладки ({error}), откат снимка…",
    )
    rollback = paths["rollbacks"] / job.id
    try:
        if not job.extra.get("host_updater") and rollback.is_dir():
            restore_snapshot_tree(
                rollback,
                database_path=sqlite_path_from_url(database_url),
                config_targets=config_files,
                data_dir=data_dir,
                preserve_dirs=[paths["root"]],
            )
    except Exception:  # noqa: BLE001
        logger.exception("Rollback after failed rebuild failed")
        error = "rollback_failed"
    job.state = STATE_FAILED
    job.phase = "failed"
    job.error = error
    save_job(ops_dir, job)
