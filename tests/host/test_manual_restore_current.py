"""Restore a current-format snapshot against an isolated installed host layout."""

import hashlib
import io
import json
from datetime import UTC, datetime
from uuid import uuid4
from zipfile import ZipFile

import pytest
from robopark_api.services.ops.archives import build_archive
from robopark_host import restore
from robopark_host.restore import run_restore
from robopark_host.retention import require_capacity


class RestoreRunner:
    def __init__(self, paths, *, fail_first_restore=False):
        self.paths = paths
        self.commands = []
        self.restored = []
        self.database = "live"
        self.fail_first_restore = fail_first_restore

    def run(self, argv, *, timeout, cwd=None, env=None, capture=False):
        self.commands.append(argv)
        if "pg_dump" in argv:
            name = next(arg for arg in argv if arg.startswith("--file="))
            relative = name.removeprefix("--file=/host-restores/")
            destination = self.paths.state / "restores" / relative
            destination.write_bytes(b"PGDMP previous")
        if "pg_restore" in argv and "--dbname=robopark" in argv:
            source = (
                "snapshot" if any("/candidate/" in arg for arg in argv) else "previous"
            )
            if self.fail_first_restore:
                self.fail_first_restore = False
                raise RuntimeError("restore command failed")
            self.restored.append(source)
            self.database = source
        if "psql" in argv:
            return b"initial\n"
        return b""

    def wait_ready(self, *, project, config, timeout):
        return True


def prepared_restore(host_paths, tmp_path, *, fail_first_restore=False):
    release = host_paths.releases / "1.0.0"
    release.mkdir(parents=True)
    (release / "manifest.json").write_text(
        json.dumps(
            {
                "app_version": "1.0.0",
                "migration_head": "initial",
            }
        )
    )
    host_paths.current.symlink_to(release)
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text(
        "ROBOPARK_DATABASE_PROFILE=postgresql-17\n"
    )
    live = host_paths.var / "data"
    live.mkdir(parents=True)
    (live / "attachment").write_bytes(b"live attachment")
    source = tmp_path / "snapshot"
    (source / "data").mkdir(parents=True)
    (source / "data/robopark.dump").write_bytes(b"PGDMP snapshot")
    (source / "data/attachment").write_bytes(b"saved attachment")
    archive = build_archive(kind="snapshot", source_root=source, app_version="1.0.0")
    identity = str(uuid4())
    artifact = "restore-" + identity + ".zip"
    (host_paths.ops / "artifacts").mkdir(parents=True)
    (host_paths.ops / "artifacts" / artifact).write_bytes(archive)
    request = {
        "kind": "restore",
        "job_id": identity,
        "artifact": artifact,
        "sha256": hashlib.sha256(archive).hexdigest(),
        "actor_user_id": 1,
        "created_at": datetime.now(UTC).isoformat(),
    }
    runner = RestoreRunner(host_paths, fail_first_restore=fail_first_restore)
    require_capacity(host_paths, len(archive))
    return request, live, runner


def test_current_snapshot_restores_data_and_resumes_writers(host_paths, tmp_path):
    request, live, runner = prepared_restore(host_paths, tmp_path)

    result = run_restore(host_paths, request, runner)

    root = host_paths.state / "restores" / request["job_id"]
    assert result["state"] == "succeeded", (
        result,
        runner.commands,
        root.exists(),
        [path.name for path in root.iterdir()] if root.exists() else [],
    )
    assert (live / "attachment").read_bytes() == b"saved attachment"
    assert runner.restored == ["snapshot"]
    assert runner.database == "snapshot"
    assert [
        command[:3] for command in runner.commands if command[0] == "systemctl"
    ] == [
        ["systemctl", "stop", "robopark.service"],
        ["systemctl", "restart", "robopark.service"],
        ["systemctl", "restart", "robopark-tuna.service"],
    ]
    assert not (host_paths.state / "maintenance.json").exists()


@pytest.mark.parametrize("corruption", ["outer_checksum", "inner_checksum"])
def test_corrupt_snapshot_is_rejected_before_stopping_writers(
    host_paths, tmp_path, corruption
):
    request, live, runner = prepared_restore(host_paths, tmp_path)
    artifact = host_paths.ops / "artifacts" / request["artifact"]
    if corruption == "outer_checksum":
        request["sha256"] = "0" * 64
    else:
        original = artifact.read_bytes()
        output = io.BytesIO()
        with ZipFile(io.BytesIO(original)) as source, ZipFile(output, "w") as target:
            for member in source.infolist():
                body = source.read(member.filename)
                if member.filename == "data/attachment":
                    body = b"tampered attachment"
                target.writestr(member, body)
        artifact.write_bytes(output.getvalue())
        request["sha256"] = hashlib.sha256(output.getvalue()).hexdigest()

    result = run_restore(host_paths, request, runner)

    assert result["state"] == "failed"
    assert (live / "attachment").read_bytes() == b"live attachment"
    assert not any(command[:2] == ["systemctl", "stop"] for command in runner.commands)
    assert not (host_paths.state / "maintenance.json").exists()


def test_failed_database_restore_rolls_back_and_resumes_writers(host_paths, tmp_path):
    request, live, runner = prepared_restore(
        host_paths, tmp_path, fail_first_restore=True
    )

    result = run_restore(host_paths, request, runner)

    assert result["state"] == "failed"
    assert (live / "attachment").read_bytes() == b"live attachment"
    assert runner.restored == ["previous"]
    assert runner.database == "previous"
    assert not (host_paths.state / "maintenance.json").exists()


def test_interrupted_restore_recovers_previous_data_before_resuming_writers(
    host_paths,
    tmp_path,
    monkeypatch,
):
    request, live, runner = prepared_restore(host_paths, tmp_path)
    original_phase = restore._phase

    class PowerLoss(BaseException):
        pass

    def interrupt(paths, journal, phase, **changes):
        original_phase(paths, journal, phase, **changes)
        if phase == "replaced":
            raise PowerLoss()

    monkeypatch.setattr(restore, "_phase", interrupt)
    with pytest.raises(PowerLoss):
        run_restore(host_paths, request, runner)
    assert (host_paths.state / "maintenance.json").exists()
    assert (live / "attachment").read_bytes() == b"saved attachment"

    monkeypatch.setattr(restore, "_phase", original_phase)
    result = run_restore(host_paths, request, runner)
    assert result["state"] == "failed"
    assert (live / "attachment").read_bytes() == b"live attachment"
    assert runner.database == "previous"
    assert not (host_paths.state / "maintenance.json").exists()


def test_exhausted_new_claim_never_relabels_an_older_terminal_journal(host_paths):
    from robopark_host.restore import recover_restore
    from robopark_host.state import atomic_write_json
    from test_host_commands import request

    new = request(host_paths, "restore", artifact="placeholder", sha256="a" * 64)
    new["artifact"] = "restore-" + new["job_id"] + ".zip"
    old = {**new, "job_id": str(uuid4())}
    old["artifact"] = "restore-" + old["job_id"] + ".zip"
    atomic_write_json(host_paths.state / "command-request.json", new)
    atomic_write_json(
        host_paths.state / "command-attempts.json",
        {"job_id": new["job_id"], "attempts": 3},
    )
    atomic_write_json(
        host_paths.state / "restore-journal.json",
        {
            "schema": 2,
            "request": old,
            "database_profile": "postgresql-17",
            "phase": "succeeded",
            "snapshot_done": True,
            "writes_resumed": True,
            "error": None,
            "publication_degraded": False,
        },
    )

    assert recover_restore(host_paths, None, automatic=True) == 1
    saved = json.loads((host_paths.state / "restore-journal.json").read_text())
    assert saved["request"] == new
    assert saved["phase"] == "manual_recovery_required"
