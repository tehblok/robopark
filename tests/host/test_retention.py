"""Actual admission directories, replay durability, and root cleanup boundaries."""

import fcntl
import json
import os
import time
from uuid import uuid4

import pytest
from robopark_host.state import atomic_write_json


def old(path, content=b"x", seconds=10 * 86400):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (time.time() - seconds,) * 2)
    return path


def test_cleanup_bounds_real_upload_diagnostic_and_inspection_paths(host_paths):
    from robopark_host.retention import artifact_usage, retain_artifacts

    identity = str(uuid4())
    upload = old(host_paths.ops / "artifacts" / f"update-{identity}.zip", b"x" * 100)
    record = old(
        host_paths.var / "api-ops/inspections" / f"{identity}.json",
        json.dumps(
            {
                "inspection_id": identity,
                "artifact": upload.name,
            }
        ).encode(),
    )
    diagnostic = old(host_paths.ops / "public/artifacts" / f"{uuid4()}.zip", b"x" * 100)
    foreign = old(host_paths.ops / "artifacts/do-not-delete.zip", b"foreign")
    release = old(host_paths.releases / "old-release/file", b"release")
    recovery = old(host_paths.ops / "rollbacks/known/data", b"snapshot")
    before = artifact_usage(host_paths)["bytes"]
    result = retain_artifacts(host_paths, max_bytes=400)
    assert result["deleted"] >= 3
    assert all(not path.exists() for path in [upload, record, diagnostic])
    assert foreign.read_bytes() == b"foreign"
    assert release.exists() and recovery.exists()
    assert artifact_usage(host_paths)["bytes"] < before


def test_active_approved_recent_and_symlink_targets_are_never_deleted(host_paths, tmp_path):
    from robopark_host.retention import retain_artifacts

    identity = str(uuid4())
    active = old(host_paths.ops / "artifacts" / f"update-{identity}.zip")
    atomic_write_json(host_paths.ops / "inbox/approved.json", {"artifact": active.name})
    recent_id = str(uuid4())
    recent = old(host_paths.ops / "artifacts" / f"update-{recent_id}.zip", seconds=5)
    outside = old(tmp_path / "outside", b"safe")
    (host_paths.ops / "artifacts" / f"update-{uuid4()}.zip").symlink_to(outside)
    result = retain_artifacts(host_paths, max_bytes=0)
    assert result["pressure"]
    assert active.exists() and recent.exists() and outside.read_bytes() == b"safe"


def test_cleanup_will_not_follow_replaced_directory_or_lock(host_paths, tmp_path):
    from robopark_host.retention import retain_artifacts

    outside = tmp_path / "outside"
    victim = old(outside / f"update-{uuid4()}.zip")
    host_paths.ops.mkdir(parents=True)
    (host_paths.ops / "artifacts").symlink_to(outside)
    api = host_paths.var / "api-ops"
    api.mkdir()
    (api / "begin.lock").symlink_to(victim)
    result = retain_artifacts(host_paths, max_bytes=0)
    assert result["blocked"]
    assert victim.exists()


def test_host_lock_makes_scheduled_retention_skip_without_waiting(host_paths):
    from robopark_host.retention import retain_artifacts

    victim = old(host_paths.ops / "artifacts" / f"update-{uuid4()}.zip")
    host_paths.lock_dir.mkdir(parents=True, exist_ok=True)
    with host_paths.host_lock.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert retain_artifacts(host_paths)["blocked"]
    assert victim.exists()


def test_compacted_receipt_still_rejects_changed_timestamp_replay(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.retention import retain_artifacts
    from test_host_commands import request

    job = request(host_paths, "repair")
    (host_paths.ops / "inbox/approved.json").unlink()
    receipt = old(
        host_paths.state / "command-receipts" / (job["job_id"] + ".json"),
        json.dumps({"request": job, "result": {"state": "succeeded"}}).encode(),
    )
    assert retain_artifacts(host_paths)["deleted"] == 1
    assert not receipt.exists()
    tombstone = host_paths.state / "retired-commands.json"
    assert tombstone.stat().st_mode & 0o777 == 0o600
    # Replay identity is UUID-bound, not timestamp-bound; a fresh timestamp cannot revive it.
    request(host_paths, "repair", job_id=job["job_id"])
    monkeypatch.setattr(commands, "run_doctor", lambda *a: pytest.fail("retired command replayed"))
    assert commands.consume_commands(host_paths, None, None) == 1


def test_corrupt_or_saturated_tombstones_preserve_receipts_and_fail_closed(host_paths, monkeypatch):
    from robopark_host import commands, retention
    from test_host_commands import request

    job = request(host_paths, "repair")
    receipt = old(
        host_paths.state / "command-receipts" / (job["job_id"] + ".json"),
        json.dumps({"request": job, "result": {}}).encode(),
    )
    tombstone = host_paths.state / "retired-commands.json"
    atomic_write_json(tombstone, {"format": 1, "bits": "ff" * retention.BLOOM_BYTES})
    assert retention.retain_artifacts(host_paths)["blocked"]
    assert receipt.exists()
    tombstone.write_text("{broken")
    assert retention.retain_artifacts(host_paths)["blocked"]
    monkeypatch.setattr(
        commands, "run_doctor", lambda *a: pytest.fail("corrupt replay store accepted")
    )
    assert commands.consume_commands(host_paths, None, None) == 1
    assert receipt.exists()


def test_scheduled_doctor_wires_retention_and_reports_actual_usage(host_paths, monkeypatch):
    from robopark_host import cli
    from robopark_host.checks import DiagnosticReport
    from robopark_host.doctor import _artifact_check

    victim = old(host_paths.ops / "public/artifacts" / f"{uuid4()}.zip", b"x" * 100)
    check = _artifact_check(host_paths, lambda *a, **kw: pytest.fail("legacy du path used"))
    assert "100" in check.message
    monkeypatch.setattr(cli, "run_doctor", lambda *a: DiagnosticReport([]))
    cli._doctor_handler(host_paths)
    assert not victim.exists()


def test_terminal_update_does_not_repeat_updater_image_cleanup(host_paths, monkeypatch):
    from robopark_host import cli, image_retention, retention

    calls = []
    monkeypatch.setattr(retention, "retain_artifacts", lambda paths: calls.append("artifacts"))
    monkeypatch.setattr(
        image_retention,
        "maintenance",
        lambda paths, runner: calls.append("maintenance") or {"blocked": False, "builder_cache": {}},
    )

    cli._retain_after_terminal_update(host_paths, "current_healthy")
    image_retention.scheduled(host_paths, object())

    assert calls == ["artifacts", "maintenance"]


def test_compaction_crash_cannot_forget_identity(host_paths, monkeypatch):
    from robopark_host import retention

    identity = str(uuid4())
    receipt = old(
        host_paths.state / "command-receipts" / (identity + ".json"),
        json.dumps({"request": {"job_id": identity}, "result": {}}).encode(),
    )

    def interrupted(entry):
        raise OSError("simulated power loss")

    monkeypatch.setattr(retention, "_remove", interrupted)
    assert retention.retain_artifacts(host_paths)["blocked"]
    assert receipt.exists()
    assert retention.command_retired(host_paths, identity)


def test_tombstone_wrong_mode_or_symlink_rejects_command(host_paths, tmp_path):
    from robopark_host.retention import command_retired

    path = host_paths.state / "retired-commands.json"
    old(path, b"{}")
    path.chmod(0o644)
    assert command_retired(host_paths, str(uuid4()))
    path.unlink()
    path.symlink_to(tmp_path / "missing")
    assert command_retired(host_paths, str(uuid4()))


def test_retired_command_publishes_terminal_failure_for_api(host_paths, monkeypatch):
    from robopark_host import commands, retention
    from test_host_commands import request

    command = request(host_paths, "repair")
    monkeypatch.setattr(retention, "command_retired", lambda *a: True)
    assert commands.consume_commands(host_paths, None, None) == 1
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["job_id"] == command["job_id"]
    assert result["state"] == "failed"
    assert result["error"] == "command_retired"
    assert not (host_paths.state / "command-request.json").exists()


def test_byte_pressure_removes_expired_previews_before_seven_day_age(host_paths):
    from robopark_host.retention import retain_artifacts

    victim = old(
        host_paths.ops / "artifacts" / f"update-{uuid4()}.zip", b"x" * 400, seconds=2 * 86400
    )
    result = retain_artifacts(host_paths, max_bytes=300)
    assert not victim.exists()
    assert not result["pressure"]


def test_active_api_approved_inspection_is_protected_during_dispatch_gap(host_paths):
    from robopark_host.retention import retain_artifacts

    identity = str(uuid4())
    artifact = old(host_paths.ops / "artifacts" / f"update-{identity}.zip")
    record = old(host_paths.var / "api-ops/inspections" / f"{identity}.json")
    atomic_write_json(
        host_paths.var / "api-ops/job.json",
        {
            "id": str(uuid4()),
            "state": "running",
            "extra": {"inspection_id": identity},
        },
    )
    retain_artifacts(host_paths, max_bytes=0)
    assert artifact.exists() and record.exists()


def test_diagnostics_refuses_capacity_before_export(host_paths, monkeypatch):
    from robopark_host import commands, retention
    from test_host_commands import request

    request(host_paths, "diagnostics")
    monkeypatch.setattr(retention, "MAX_BYTES", 1)
    monkeypatch.setattr(
        commands, "create_diagnostic_bundle", lambda *a: pytest.fail("disk export started")
    )
    assert commands.consume_commands(host_paths, lambda *a, **k: None, None) == 1
    assert not list((host_paths.ops / "public/artifacts").glob("*.zip"))


def test_restore_recovery_contract_is_outside_cleanup_namespace(host_paths):
    from robopark_host.retention import retain_artifacts

    identity = str(uuid4())
    files = [
        old(host_paths.state / "restores" / identity / name)
        for name in ("snapshot.zip", "candidate/data", "previous/data")
    ]
    files += [
        old(host_paths.var / (prefix + identity) / "data")
        for prefix in (".manual-restore-", ".manual-displaced-")
    ]
    files += [old(host_paths.state / name) for name in ("restore-journal.json", "restore-check")]
    retain_artifacts(host_paths, max_bytes=0)
    assert all(path.exists() for path in files)


def test_missing_compacted_tombstone_never_reinitializes_empty_history(host_paths):
    from robopark_host import retention

    identity = str(uuid4())
    old(
        host_paths.state / "command-receipts" / (identity + ".json"),
        json.dumps({"request": {"job_id": identity}, "result": {}}).encode(),
    )
    retention.retain_artifacts(host_paths)
    (host_paths.state / "retired-commands.json").unlink()
    assert retention.command_retired(host_paths, identity)
    assert retention.retain_artifacts(host_paths)["blocked"]


def test_unrecognized_receipt_content_is_not_destroyed(host_paths):
    from robopark_host.retention import retain_artifacts

    target = old(
        host_paths.state / "command-receipts" / (str(uuid4()) + ".json"), b'{"foreign":"keep"}'
    )
    assert retain_artifacts(host_paths)["blocked"]
    assert target.exists()


def test_busy_host_is_not_diagnosed_as_storage_failure(host_paths):
    from robopark_host.doctor import _artifact_check
    from robopark_host.retention import retain_artifacts

    host_paths.ops.mkdir(parents=True)
    host_paths.lock_dir.mkdir(parents=True, exist_ok=True)
    with host_paths.host_lock.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        retain_artifacts(host_paths)
    assert _artifact_check(host_paths, None).status == "warning"


def test_crashed_upload_temporary_file_is_bounded_and_cleaned(host_paths):
    from robopark_host.retention import retain_artifacts

    target = old(host_paths.ops / "artifacts/.bridge-abcdefgh", b"x" * 400)
    record = old(host_paths.var / "api-ops/inspections/.bridge-1234abcd", b"{}")
    assert retain_artifacts(host_paths)["deleted"] == 2
    assert not target.exists() and not record.exists()


def test_scheduled_cleanup_reclaims_only_stale_owned_staging_work(host_paths, tmp_path):
    from robopark_host.retention import retain_artifacts

    stale = []
    for name in (str(uuid4()), f"local-updater-{uuid4()}"):
        path = host_paths.ops / "staging" / name
        old(path / "work", seconds=2 * 86400)
        os.utime(path, (time.time() - 2 * 86400,) * 2)
        stale.append(path)
    active_id = str(uuid4())
    active = host_paths.ops / "staging" / active_id
    old(active / "work", seconds=2 * 86400)
    os.utime(active, (time.time() - 2 * 86400,) * 2)
    atomic_write_json(host_paths.state / "updater-journal.json", {"job_id": active_id})
    recent = host_paths.ops / "staging" / str(uuid4())
    old(recent / "work", seconds=60)
    foreign = host_paths.ops / "staging/keep-me"
    old(foreign / "work", seconds=2 * 86400)
    outside = old(tmp_path / "outside", b"safe", seconds=2 * 86400)
    (host_paths.ops / "staging" / f"local-updater-{uuid4()}").symlink_to(outside)

    result = retain_artifacts(host_paths)

    assert result["staging_deleted"] == 2
    assert all(not path.exists() for path in stale)
    assert active.exists() and recent.exists() and foreign.exists()
    assert outside.read_bytes() == b"safe"


def test_cleanup_reclaims_only_expired_exact_operation_residue(host_paths, tmp_path):
    from robopark_host.retention import retain_artifacts

    inactive = str(uuid4())
    active = str(uuid4())
    atomic = old(host_paths.state / "image-owned" / f".{inactive}.json.abcdefgh")
    receipt_atomic = old(
        host_paths.state / "successful-releases" / f".{inactive}.json.abcdefgh"
    )
    diagnostic = old(host_paths.root / "var/log/robopark/.doctor-abcdefgh")
    fresh = old(
        host_paths.state / "image-owned" / f".{uuid4()}.json.abcdefgh", seconds=60
    )
    active_atomic = old(host_paths.state / "image-owned" / f".{active}.json.abcdefgh")
    atomic_write_json(host_paths.state / "updater-journal.json", {"job_id": active})
    foreign = old(host_paths.state / "image-owned" / ".foreign.json.abcdefgh")
    business_photo = old(host_paths.var / "data/photos/keep.jpg")
    outside = old(tmp_path / "outside")
    wrong_directory = old(host_paths.var / "diagnostics/.doctor-abcdefgh")
    symlink = host_paths.root / "var/log/robopark/.doctor-hgfedcba"
    symlink.symlink_to(outside)

    result = retain_artifacts(host_paths)

    assert result["staging_deleted"] == 0
    assert result["atomic_deleted"] == 2
    assert result["diagnostic_deleted"] == 1
    assert result["temporary_deleted"] == 3
    assert not atomic.exists() and not receipt_atomic.exists() and not diagnostic.exists()
    assert fresh.exists() and active_atomic.exists()
    assert (
        foreign.exists()
        and wrong_directory.exists()
        and business_photo.exists()
        and outside.exists()
        and symlink.is_symlink()
    )
