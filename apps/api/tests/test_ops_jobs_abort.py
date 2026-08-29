from pathlib import Path

import pytest

from robopark_api.services.ops.jobs import (
    STATE_FAILED,
    STATE_RUNNING,
    JobAborted,
    abort_job,
    load_job,
    new_job,
    save_job,
)


def test_save_job_does_not_resurrect_aborted(tmp_path: Path):
    ops = tmp_path / "ops"
    job = new_job("restore", exempt_token_hash="x")
    job.state = STATE_RUNNING
    save_job(ops, job)
    aborted = abort_job(ops)
    assert aborted is not None
    assert aborted.state == STATE_FAILED

    job.phase = "unpacking"
    with pytest.raises(JobAborted):
        save_job(ops, job)

    disk = load_job(ops)
    assert disk is not None
    assert disk.state == STATE_FAILED
    assert disk.error == "aborted"
    assert disk.phase == "failed"


def test_save_job_allows_terminal_failed_write_after_abort(tmp_path: Path):
    """A later failed save must not overwrite abort; disk error stays aborted."""
    ops = tmp_path / "ops"
    job = new_job("update", exempt_token_hash="x")
    job.state = STATE_RUNNING
    save_job(ops, job)
    abort_job(ops)
    job.state = STATE_FAILED
    job.error = "tests_failed"
    job.phase = "failed"
    with pytest.raises(JobAborted):
        save_job(ops, job)
    disk = load_job(ops)
    assert disk is not None
    assert disk.state == STATE_FAILED
    assert disk.error == "aborted"
