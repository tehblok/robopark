"""Update/restore orchestration: tests-then-cutover, rollback, kind checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from robopark_api.services.ops import runner as runner_mod
from robopark_api.services.ops.archives import KIND_RELEASE, KIND_SNAPSHOT, build_archive
from robopark_api.services.ops.jobs import (
    PHASE_AWAITING_REBUILD,
    STATE_FAILED,
    STATE_RUNNING,
    STATE_SUCCEEDED,
    abort_job,
    load_job,
    save_job,
)
from robopark_api.services.ops.runner import (
    UPDATE_PHRASE,
    JobConflict,
    OpsContext,
    OpsError,
    ReleaseTestsFailed,
    start_and_run,
)
from robopark_api.services.ops.snapshot import build_snapshot_tree

_RELEASE_PRIVATE = Ed25519PrivateKey.generate()
_RELEASE_PRIVATE_KEY = _RELEASE_PRIVATE.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_RELEASE_PUBLIC_KEY = _RELEASE_PRIVATE.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
)


def _ctx(tmp_path: Path, db: Path, apply_root: Path | None = None, **kwargs) -> OpsContext:
    env = tmp_path / "host.env"
    env.write_text("SECRET_KEY=k\n", encoding="utf-8")
    kwargs.setdefault("use_ops_agent", False)
    kwargs.setdefault("release_public_key", _RELEASE_PUBLIC_KEY)
    return OpsContext(
        ops_dir=tmp_path / "ops",
        database_url=f"sqlite:///{db}",
        config_files={"host.env": env},
        data_dir=db.parent,
        apply_root=apply_root,
        app_version="test",
        **kwargs,
    )


def _tiny_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"db-v1")


def _release_zip(tmp_path: Path, *, tests_ok: bool) -> bytes:
    root = tmp_path / "payload"
    if root.exists():
        raise AssertionError("payload already built")
    api = root / "apps" / "api"
    (api / "src" / "pkg").mkdir(parents=True)
    (api / "src" / "pkg" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    (api / "tests").mkdir()
    body = "def test_ok():\n    assert True\n" if tests_ok else "def test_ok():\n    assert False\n"
    (api / "tests" / "test_ok.py").write_text(body, encoding="utf-8")
    return build_archive(
        kind=KIND_RELEASE,
        source_root=root,
        app_version="2",
        release_meta={"git_sha": "a" * 40, "migration_head": "0017_driver_work_reports"},
        signing_key=_RELEASE_PRIVATE_KEY,
    )


def test_failed_tests_do_not_copy_files(tmp_path: Path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    apply = tmp_path / "dest"
    apply.mkdir()
    (apply / "keep.txt").write_text("old", encoding="utf-8")

    def boom(_staging: Path) -> str:
        raise ReleaseTestsFailed("boom")

    ctx = _ctx(tmp_path, db, apply, test_runner=boom)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="abc",
        archive=_release_zip(tmp_path, tests_ok=False),
        confirm=UPDATE_PHRASE,
    )
    assert job.state == STATE_FAILED
    assert job.error == "tests_failed"
    assert (apply / "keep.txt").read_text(encoding="utf-8") == "old"
    assert not (apply / "apps").exists()


def test_successful_update_copies_tree(tmp_path: Path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    apply = tmp_path / "dest"
    ctx = _ctx(tmp_path, db, apply, test_runner=lambda _staging: "ok")
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="abc",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert job.state == STATE_SUCCEEDED
    assert (apply / "apps" / "api" / "src" / "pkg" / "mod.py").read_text(
        encoding="utf-8"
    ) == "VALUE = 2\n"


def test_snapshot_archive_rejected_as_update(tmp_path: Path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    env = tmp_path / "host.env"
    env.write_text("SECRET_KEY=k\n", encoding="utf-8")
    tree = tmp_path / "snap-tree"
    build_snapshot_tree(
        tree,
        database_url=f"sqlite:///{db}",
        config_files={"host.env": env},
        data_dir=db.parent,
    )
    archive = build_archive(kind=KIND_SNAPSHOT, source_root=tree, app_version="1")
    apply = tmp_path / "dest"
    ctx = _ctx(tmp_path, db, apply, test_runner=lambda _s: "ok")
    job = start_and_run(
        ctx, "update", exempt_token_hash="x", archive=archive, confirm=UPDATE_PHRASE
    )
    assert job.state == STATE_FAILED
    assert job.error == "unexpected_kind"
    assert not apply.exists()


def test_unhealthy_cutover_restores_sqlite_and_files(tmp_path: Path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    apply = tmp_path / "dest"
    apply.mkdir()
    (apply / "keep.txt").write_text("old", encoding="utf-8")
    ctx = _ctx(tmp_path, db, apply, test_runner=lambda _s: "ok", health_check=lambda: False)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert job.state == STATE_FAILED
    assert job.error == "cutover_unhealthy"
    assert db.read_bytes() == b"db-v1"
    assert (apply / "keep.txt").read_text(encoding="utf-8") == "old"
    assert not (apply / "apps" / "api" / "src" / "pkg" / "mod.py").exists()


def test_second_job_conflict(tmp_path: Path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    ctx = _ctx(tmp_path, db, tmp_path / "dest", test_runner=lambda _s: "ok")
    start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    start_and_run(ctx, "snapshot", exempt_token_hash="x")
    job = load_job(ctx.ops_dir)
    assert job is not None
    job.state = STATE_RUNNING
    save_job(ctx.ops_dir, job)
    with pytest.raises(JobConflict):
        start_and_run(ctx, "snapshot", exempt_token_hash="x")


def test_restore_requires_phrase(tmp_path: Path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    ctx = _ctx(tmp_path, db)
    with pytest.raises(OpsError, match="confirm_required"):
        start_and_run(ctx, "restore", exempt_token_hash="x", archive=b"PK\x03\x04", confirm="nope")
    assert load_job(ctx.ops_dir) is None


def test_ops_agent_path_leaves_job_running(tmp_path: Path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    apply = tmp_path / "dest"
    ctx = _ctx(tmp_path, db, apply, test_runner=lambda _s: "ok", use_ops_agent=True)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert job.state == STATE_RUNNING
    assert job.phase == "awaiting_rebuild"
    flag = ctx.ops_dir / "rebuild.requested"
    assert flag.is_file()
    assert str(ctx.ops_dir / "staging" / "release") in flag.read_text(encoding="utf-8")
    assert not (apply / "apps").exists()


def test_reconcile_failed_rebuild_rolls_back(tmp_path: Path):
    from robopark_api.services.ops.reconcile import reconcile_pending_rebuild

    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    apply = tmp_path / "dest"
    ctx = _ctx(tmp_path, db, apply, test_runner=lambda _s: "ok", use_ops_agent=True)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    (ctx.ops_dir / "rebuild.result").write_text(
        f'{{"job_id":"{job.id}","ok":false,"error":"compose_failed"}}',
        encoding="utf-8",
    )
    reconcile_pending_rebuild(
        ctx.ops_dir,
        database_url=ctx.database_url,
        config_files=ctx.config_files,
        data_dir=ctx.data_dir,
    )
    done = load_job(ctx.ops_dir)
    assert done is not None
    assert done.state == STATE_FAILED
    assert done.error == "compose_failed"
    assert db.read_bytes() == b"db-v1"


def test_abort_during_test_runner_keeps_aborted(tmp_path: Path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    apply = tmp_path / "dest"
    apply.mkdir()
    (apply / "keep.txt").write_text("old", encoding="utf-8")
    ctx = _ctx(tmp_path, db, apply, test_runner=lambda _s: "ok")

    def abort_then_fail(_staging: Path) -> str:
        aborted = abort_job(ctx.ops_dir)
        assert aborted is not None
        raise ReleaseTestsFailed("should_not_overwrite_abort")

    ctx.test_runner = abort_then_fail
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert job.state == STATE_FAILED
    assert job.error == "aborted"
    disk = load_job(ctx.ops_dir)
    assert disk is not None
    assert disk.state == STATE_FAILED
    assert disk.error == "aborted"
    assert (apply / "keep.txt").read_text(encoding="utf-8") == "old"
    assert not (apply / "apps").exists()


def test_abort_during_awaiting_rebuild_does_not_leave_rebuild_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    apply = tmp_path / "dest"
    ctx = _ctx(tmp_path, db, apply, test_runner=lambda _s: "ok", use_ops_agent=True)
    original = runner_mod.save_job

    def save_then_abort_on_awaiting(ops_dir: Path, job) -> None:
        original(ops_dir, job)
        if job.phase == PHASE_AWAITING_REBUILD and job.state == STATE_RUNNING:
            aborted = abort_job(ops_dir)
            assert aborted is not None

    monkeypatch.setattr(runner_mod, "save_job", save_then_abort_on_awaiting)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert job.state == STATE_FAILED
    assert job.error == "aborted"
    assert not (ctx.ops_dir / "rebuild.requested").exists()


def test_host_update_delegates_verified_artifact_without_container_snapshot_or_tests(tmp_path):
    import json

    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)

    def no_container_tests(_staging):
        raise AssertionError("host must own candidate testing")

    ctx = _ctx(
        tmp_path, db, None, test_runner=no_container_tests, use_host_updater=True, actor_user_id=17
    )
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert job.phase == "awaiting_rebuild"
    request = json.loads((ctx.ops_dir / "inbox/approved.json").read_text())
    assert set(request) == {"job_id", "kind", "artifact", "actor_user_id", "created_at"}
    assert request["job_id"] == job.id
    assert request["kind"] == "update"
    assert request["actor_user_id"] == 17
    assert (ctx.ops_dir / "artifacts" / request["artifact"]).is_file()
    assert not (ctx.ops_dir / "staging/release").exists()
    assert not (ctx.ops_dir / "rollbacks" / job.id).exists()


def test_host_update_requires_authenticated_actor(tmp_path):
    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    ctx = _ctx(tmp_path, db, None, use_host_updater=True)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert job.error == "actor_required"
    assert not (ctx.ops_dir / "inbox/approved.json").exists()


def test_host_failure_reconciliation_never_restores_data_a_second_time(tmp_path):
    import json

    from robopark_api.services.ops.reconcile import reconcile_pending_rebuild

    db = tmp_path / "data" / "robopark.db"
    _tiny_db(db)
    ctx = _ctx(tmp_path, db, None, use_host_updater=True, actor_user_id=17)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    # This is the host rollback directory, not the old container snapshot schema.
    snapshot = ctx.ops_dir / "rollbacks" / job.id / "data"
    snapshot.mkdir(parents=True)
    (snapshot / "robopark.db").write_bytes(b"old-snapshot")
    db.write_bytes(b"new-write-after-host-rollback")
    (ctx.ops_dir / "public").mkdir()
    (ctx.ops_dir / "public/rebuild.result").write_text(
        json.dumps({"job_id": job.id, "ok": False, "error": "cutover_unhealthy"})
    )
    reconcile_pending_rebuild(
        ctx.ops_dir,
        database_url=ctx.database_url,
        config_files=ctx.config_files,
        data_dir=ctx.data_dir,
    )
    assert db.read_bytes() == b"new-write-after-host-rollback"
    assert load_job(ctx.ops_dir).error == "cutover_unhealthy"


def test_host_reconciliation_reads_public_result_without_unlinking_root_owned_file(tmp_path):
    import json

    from robopark_api.services.ops.reconcile import reconcile_pending_rebuild

    db = tmp_path / "data/robopark.db"
    _tiny_db(db)
    ctx = _ctx(tmp_path, db, None, use_host_updater=True, actor_user_id=17)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    public = ctx.ops_dir / "public/rebuild.result"
    public.parent.mkdir()
    public.write_text(json.dumps({"job_id": job.id, "ok": True, "error": None}))
    public.chmod(0o444)
    reconcile_pending_rebuild(
        ctx.ops_dir,
        database_url=ctx.database_url,
        config_files=ctx.config_files,
        data_dir=ctx.data_dir,
    )
    assert load_job(ctx.ops_dir).state == STATE_SUCCEEDED
    assert public.exists()


def test_host_approval_and_result_use_separate_mount_from_api_jobs(tmp_path):
    import json

    from robopark_api.services.ops.reconcile import reconcile_pending_rebuild

    db = tmp_path / "data/robopark.db"
    _tiny_db(db)
    host_ops = tmp_path / "host-ops"
    ctx = _ctx(tmp_path, db, None, use_host_updater=True, actor_user_id=17, host_ops_dir=host_ops)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert (ctx.ops_dir / "job.json").is_file()
    assert not (ctx.ops_dir / "inbox/approved.json").exists()
    approval = json.loads((host_ops / "inbox/approved.json").read_text())
    assert (host_ops / "artifacts" / approval["artifact"]).is_file()
    assert not (host_ops / "staging").exists()
    assert not (host_ops / "rollbacks").exists()
    public = host_ops / "public/rebuild.result"
    public.parent.mkdir()
    public.write_text(json.dumps({"job_id": job.id, "ok": True, "error": None}))
    reconcile_pending_rebuild(
        ctx.ops_dir,
        database_url=ctx.database_url,
        config_files=ctx.config_files,
        data_dir=ctx.data_dir,
        host_ops_dir=host_ops,
    )
    assert load_job(ctx.ops_dir).state == STATE_SUCCEEDED


def test_abort_while_host_approval_is_queued_leaves_no_approval(tmp_path, monkeypatch):
    db = tmp_path / "data/robopark.db"
    _tiny_db(db)
    ctx = _ctx(tmp_path, db, None, use_host_updater=True, actor_user_id=17)
    original = runner_mod.save_job

    def abort(ops_dir, job):
        original(ops_dir, job)
        if job.phase == PHASE_AWAITING_REBUILD:
            abort_job(ops_dir)

    monkeypatch.setattr(runner_mod, "save_job", abort)
    job = start_and_run(
        ctx,
        "update",
        exempt_token_hash="x",
        archive=_release_zip(tmp_path, tests_ok=True),
        confirm=UPDATE_PHRASE,
    )
    assert job.error == "aborted"
    assert not (ctx.ops_dir / "inbox/approved.json").exists()
