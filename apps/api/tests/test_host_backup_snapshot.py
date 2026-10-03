"""Host backup snapshots must not replace or conflict with their own operation."""

import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from robopark_api.services.ops import scheduled_snapshot
from robopark_api.services.ops.archives import inspect_archive
from robopark_api.services.ops.jobs import load_job, new_job, save_job
from robopark_api.services.ops.runner import OpsContext


@pytest.mark.parametrize("authorized", [True, False])
def test_parent_backup_snapshot_preserves_host_operation_and_ack_removes_copy(
    tmp_path, monkeypatch, capsys, authorized
):
    database = tmp_path / "database.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE records (value TEXT)")
        connection.execute("INSERT INTO records VALUES ('preserved')")
    ctx = OpsContext(ops_dir=tmp_path / "ops", database_url=f"sqlite:///{database}")
    parent = new_job("backup", exempt_token_hash="test-session")
    parent.state = "running"
    parent.extra = {
        "host_updater": True,
        "host_request": {
            "job_id": parent.id,
            "kind": "backup",
            "actor_user_id": 1,
            "authorization": {
                "operation_id": parent.id,
                "operation_kind": "backup",
                "actor_user_id": 1,
                "consumed": authorized,
            },
        },
    }
    save_job(ctx.ops_dir, parent)
    original = (ctx.ops_dir / "job.json").read_bytes()
    monkeypatch.setattr(scheduled_snapshot, "build_ops_context", lambda _: ctx)
    monkeypatch.setattr(sys, "argv", ["scheduled_snapshot", "--parent-operation", parent.id])
    if not authorized:
        with pytest.raises(RuntimeError, match="parent_backup_required"):
            scheduled_snapshot.main()
        assert (ctx.ops_dir / "job.json").read_bytes() == original
        return
    scheduled_snapshot.main()
    artifact = Path(capsys.readouterr().out.strip())
    assert artifact.name == f"snapshot-{parent.id}.zip"
    with artifact.open("rb") as stream:
        assert inspect_archive(stream).kind == "snapshot"
    assert (ctx.ops_dir / "job.json").read_bytes() == original
    assert load_job(ctx.ops_dir).id == parent.id
    monkeypatch.setattr(sys, "argv", ["scheduled_snapshot", "--ack", artifact.name])
    scheduled_snapshot.main()
    assert not artifact.exists()
    assert scheduled_snapshot._tracked(ctx.ops_dir) == []
    assert (ctx.ops_dir / "job.json").read_bytes() == original


def test_parent_snapshot_cannot_bypass_a_different_active_operation(tmp_path, monkeypatch):
    ctx = OpsContext(ops_dir=tmp_path / "ops", database_url="sqlite:///unused.db")
    parent = new_job("reboot", exempt_token_hash="test-session")
    parent.state = "running"
    save_job(ctx.ops_dir, parent)
    monkeypatch.setattr(scheduled_snapshot, "build_ops_context", lambda _: ctx)
    monkeypatch.setattr(sys, "argv", ["scheduled_snapshot", "--parent-operation", str(uuid4())])
    with pytest.raises(RuntimeError, match="parent_backup_required"):
        scheduled_snapshot.main()
