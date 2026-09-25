"""Snapshot/restore orchestration retained after legacy release ZIP retirement."""

from pathlib import Path

import pytest

from robopark_api.services.ops.jobs import STATE_RUNNING, JobConflict, load_job, save_job
from robopark_api.services.ops.runner import OpsContext, OpsError, start_and_run


def _ctx(tmp_path: Path) -> OpsContext:
    data = tmp_path / "data"
    data.mkdir()
    database = data / "robopark.db"
    database.write_bytes(b"db-v1")
    env = tmp_path / "host.env"
    env.write_text("SECRET_KEY=k\n", encoding="utf-8")
    return OpsContext(
        ops_dir=tmp_path / "ops",
        database_url=f"sqlite:///{database}",
        config_files={"host.env": env},
        data_dir=data,
        app_version="test",
        use_ops_agent=False,
    )


def test_restore_requires_phrase_before_creating_job(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with pytest.raises(OpsError, match="confirm_required"):
        start_and_run(
            ctx,
            "restore",
            exempt_token_hash="x",
            archive=b"PK\x03\x04",
            confirm="nope",
        )
    assert load_job(ctx.ops_dir) is None


def test_running_job_blocks_second_operation(tmp_path: Path):
    ctx = _ctx(tmp_path)
    start_and_run(ctx, "snapshot", exempt_token_hash="x")
    job = load_job(ctx.ops_dir)
    assert job is not None
    job.state = STATE_RUNNING
    save_job(ctx.ops_dir, job)
    with pytest.raises(JobConflict):
        start_and_run(ctx, "snapshot", exempt_token_hash="x")
