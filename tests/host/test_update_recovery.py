import hashlib
import json
import os
import zipfile
from uuid import uuid4

import pytest
import robopark_host.updater as updater
from robopark_host.updater import apply_release, recover_interrupted_update
from test_updater import host as updater_host

host = updater_host


class SimulatedPowerLoss(BaseException):
    pass


@pytest.mark.parametrize(
    "phase",
    [
        "unpacking",
        "unpacked",
        "building",
        "built",
        "smoking",
        "smoked",
        "maintenance",
        "stopping",
        "snapshotting",
        "snapshotted",
        "tools_staging",
        "tools_staged",
        "publishing",
        "published",
        "switching",
        "switched",
        "migrating",
        "migrated",
        "starting",
        "started",
        "activating",
        "activated",
        "health_check",
        "healthy",
        "reconciling",
        "publication",
        "publication_checked",
        "resuming",
        "succeeded",
    ],
)
def test_power_loss_recovers_to_verified_release(host, monkeypatch, phase):
    original = updater.atomic_write_json
    tripped = False

    def interrupt(path, payload, *args, **kwargs):
        nonlocal tripped
        original(path, payload, *args, **kwargs)
        if path.name == "updater-journal.json" and payload.get("phase") == phase and not tripped:
            tripped = True
            raise SimulatedPowerLoss()

    monkeypatch.setattr(updater, "atomic_write_json", interrupt)
    with pytest.raises(SimulatedPowerLoss):
        apply_release(host.request(), host.paths, host.runner)
        updater.reconcile_after_exit(host.paths, host.runner)
    assert tripped
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.state in {"current_healthy", "previous_restored"}
    assert host.paths.current.resolve(strict=True).exists()
    assert (host.paths.state / "current-compose.json").resolve(strict=True).exists()
    assert not (host.paths.state / "maintenance.json").exists()
    again = recover_interrupted_update(host.paths, host.runner)
    assert again.state in {"current_healthy", "previous_restored"}


def test_recovery_never_restores_data_after_writes_resume(host, monkeypatch):
    apply_release(host.request(), host.paths, host.runner)
    updater.reconcile_after_exit(host.paths, host.runner)
    (host.paths.var / "data/robopark.db").write_text("new-user-write")
    host.runner.health = False
    result = recover_interrupted_update(host.paths, host.runner)
    assert (host.paths.var / "data/robopark.db").read_text() == "new-user-write"
    assert result.state == "current_healthy"


def test_recovery_rejects_tampered_previous_release(host, monkeypatch):
    original = updater.atomic_write_json

    def interrupt(path, payload, *args, **kwargs):
        original(path, payload, *args, **kwargs)
        if path.name == "updater-journal.json" and payload.get("phase") == "switched":
            raise SimulatedPowerLoss()

    monkeypatch.setattr(updater, "atomic_write_json", interrupt)
    with pytest.raises(SimulatedPowerLoss):
        apply_release(host.request(), host.paths, host.runner)
    monkeypatch.setattr(updater, "atomic_write_json", original)
    (host.paths.releases / "1.0.0/scripts/verify.sh").write_text("tampered")
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.error == "manual_recovery_required"
    assert (host.paths.state / "maintenance.json").exists()


def test_corrupt_journal_fails_closed_without_arbitrary_path_operations(host):
    sentinel = host.paths.root / "sentinel"
    sentinel.write_text("keep")
    (host.paths.state / "updater-journal.json").write_text(
        json.dumps({"phase": "switched", "previous": str(sentinel)})
    )
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.error == "manual_recovery_required"
    assert sentinel.read_text() == "keep"
    assert host.runner.commands == []


def test_power_loss_resuming_third_release_still_finishes_retention(host, monkeypatch):
    apply_release(host.request(), host.paths, host.runner)
    updater.reconcile_after_exit(host.paths, host.runner)
    request = host.request(
        host.package("3.0.0", meta={"migration_head": "new", "migration_compatibility": {}})
    )
    apply_release(request, host.paths, host.runner)
    original = updater.atomic_write_json

    def interrupt(path, payload, *args, **kwargs):
        original(path, payload, *args, **kwargs)
        if path.name == "updater-journal.json" and payload.get("phase") == "resuming":
            raise SimulatedPowerLoss()

    monkeypatch.setattr(updater, "atomic_write_json", interrupt)
    with pytest.raises(SimulatedPowerLoss):
        updater.reconcile_after_exit(host.paths, host.runner)
    monkeypatch.setattr(updater, "atomic_write_json", original)
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.state == "current_healthy"
    assert len(list(host.paths.releases.iterdir())) == 3
    assert len(list((host.paths.ops / "rollbacks").iterdir())) == 2


def test_unknown_journal_phase_fails_closed(host):
    apply_release(host.request(), host.paths, host.runner)
    path = host.paths.state / "updater-journal.json"
    journal = json.loads(path.read_text())
    journal["phase"] = "not_a_valid_phase"
    path.write_text(json.dumps(journal))
    commands_before = len(host.runner.commands)
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.error == "manual_recovery_required"
    assert len(host.runner.commands) == commands_before


def test_tampered_running_candidate_rolls_back_to_verified_previous(host):
    apply_release(host.request(), host.paths, host.runner)
    (host.paths.current.resolve() / "scripts/verify.sh").write_text("tampered")
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.state == "previous_restored"
    assert host.paths.current.resolve().name == "1.0.0"
    assert (host.paths.var / "data/robopark.db").read_text() == "original"


def test_reconcile_restart_failure_rolls_back_under_maintenance(host):
    apply_release(host.request(), host.paths, host.runner)
    original = host.runner.run
    tripped = False

    def fail_once(argv, **kwargs):
        nonlocal tripped
        if argv == ["systemctl", "restart", "robopark.service"] and not tripped:
            tripped = True
            raise RuntimeError("failed")
        return original(argv, **kwargs)

    host.runner.run = fail_once
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.state == "previous_restored"
    assert host.paths.current.resolve().name == "1.0.0"


@pytest.mark.parametrize(
    "phase", ["rolling_back", "rollback_healthy", "rollback_resuming", "rolled_back"]
)
def test_power_loss_during_rollback_finishes_cleanup(host, monkeypatch, phase):
    host.runner.health = False
    original = updater.atomic_write_json
    tripped = False

    def interrupt(path, payload, *args, **kwargs):
        nonlocal tripped
        original(path, payload, *args, **kwargs)
        if path.name == "updater-journal.json" and payload.get("phase") == phase and not tripped:
            tripped = True
            raise SimulatedPowerLoss()

    monkeypatch.setattr(updater, "atomic_write_json", interrupt)
    with pytest.raises(SimulatedPowerLoss):
        apply_release(host.request(), host.paths, host.runner)
    assert tripped
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.state == "previous_restored"
    assert [p.name for p in host.paths.releases.iterdir()] == ["1.0.0"]
    assert (host.paths.var / "data/robopark.db").read_text() == "original"


def test_completed_updates_prune_old_compose_and_displaced_data(host):
    for number in (2, 3, 4):
        artifact = host.package(
            f"{number}.0.0",
            meta={
                "migration_head": "new",
                "migration_compatibility": {"from_heads": ["old"], "reversible": True},
            },
        )
        apply_release(host.request(artifact), host.paths, host.runner)
        updater.reconcile_after_exit(host.paths, host.runner)
    assert len(list((host.paths.state / "compose").iterdir())) == 3
    assert not list(host.paths.var.glob(".displaced-*"))


def test_retention_keeps_current_and_two_newest_successful_releases(host):
    releases = [f"release-{uuid4()}" for _ in range(4)]
    current, prior_one, prior_two, oldest = releases
    receipts = host.paths.state / "successful-releases"
    rollback_root = host.paths.ops / "rollbacks"
    (host.paths.state / "compose").mkdir()
    receipts.mkdir()
    rollback_root.mkdir()
    for offset, release in enumerate(releases):
        (host.paths.releases / release).mkdir()
        identifier = release.removeprefix("release-")
        (host.paths.state / "compose" / f"{identifier}-production.json").write_text("{}")
        (rollback_root / identifier / "data").mkdir(parents=True)
        receipt = receipts / f"{release}.json"
        receipt.write_text('{"successful": true}')
        receipt.chmod(0o600)
        timestamp = 1_000_000_000 + len(releases) - offset
        os.utime(receipt, ns=(timestamp, timestamp))
    host.paths.current.unlink()
    host.paths.previous.unlink(missing_ok=True)
    import shutil

    shutil.rmtree(host.paths.releases / "1.0.0")
    host.paths.current.symlink_to(host.paths.releases / current)
    host.paths.previous.symlink_to(host.paths.releases / prior_one)

    updater._retention(
        host.paths,
        {"job_id": current.removeprefix("release-"), "previous_config": "compose/initial.json"},
    )

    assert {path.name for path in host.paths.releases.iterdir()} == {current, prior_one, prior_two}
    assert not (host.paths.releases / oldest).exists()
    assert not (host.paths.state / "compose" / f"{oldest.removeprefix('release-')}-production.json").exists()
    assert not (receipts / f"{oldest}.json").exists()
    assert not (rollback_root / oldest.removeprefix("release-")).exists()
    for release in (current, prior_one, prior_two):
        identifier = release.removeprefix("release-")
        assert (host.paths.state / "compose" / f"{identifier}-production.json").exists()
        assert (receipts / f"{release}.json").exists()
        assert (rollback_root / identifier).exists()


@pytest.mark.parametrize("name", ["..json", "...json"])
def test_retention_never_treats_dot_receipt_as_a_release_path(host, monkeypatch, name):
    receipts = host.paths.state / "successful-releases"
    receipts.mkdir()
    previous = host.paths.releases / "previous"
    retained = host.paths.releases / "retained"
    previous.mkdir()
    retained.mkdir()
    host.paths.previous.symlink_to(previous)
    valid = receipts / "retained.json"
    valid.write_text('{"successful": true}')
    valid.chmod(0o600)
    dot_receipt = receipts / name
    dot_receipt.write_text('{"successful": true}')
    dot_receipt.chmod(0o600)
    os.utime(valid, ns=(2_000_000_000, 2_000_000_000))
    os.utime(dot_receipt, ns=(1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(updater.shutil, "rmtree", lambda path: pytest.fail(f"unsafe removal: {path}"))

    updater._retention(host.paths, {"job_id": str(uuid4()), "previous_config": "compose-1.0.0.json"})

    assert dot_receipt.exists()


def test_retention_scans_success_receipts_once(host, monkeypatch):
    calls = 0
    original = updater._successful_release_receipts

    def receipts(paths):
        nonlocal calls
        calls += 1
        return original(paths)

    monkeypatch.setattr(updater, "_successful_release_receipts", receipts)
    updater._retention(host.paths, {"job_id": str(uuid4()), "previous_config": "compose-1.0.0.json"})
    assert calls == 1


@pytest.mark.parametrize(
    "phase",
    ["unpacking", "unpacked", "building", "built", "smoking", "smoked"],
)
def test_pre_maintenance_recovery_only_discards_candidate(host, monkeypatch, phase):
    original = updater.atomic_write_json
    before_current = host.paths.current.readlink()
    before_config = (host.paths.state / "current-compose.json").readlink()

    def interrupt(path, payload, *args, **kwargs):
        original(path, payload, *args, **kwargs)
        if path.name == "updater-journal.json" and payload.get("phase") == phase:
            raise SimulatedPowerLoss()

    monkeypatch.setattr(updater, "atomic_write_json", interrupt)
    with pytest.raises(SimulatedPowerLoss):
        apply_release(host.request(), host.paths, host.runner)
    monkeypatch.setattr(updater, "atomic_write_json", original)
    for _ in range(2):
        result = recover_interrupted_update(host.paths, host.runner)
        assert result.error == "interrupted"
        assert not any(command[0] == "systemctl" for command in host.runner.commands)
        assert host.paths.current.readlink() == before_current
        assert (host.paths.state / "current-compose.json").readlink() == before_config
        assert (host.paths.var / "data/robopark.db").read_text() == "original"
        assert not (host.paths.state / "maintenance.json").exists()
        assert not (host.paths.ops / "public/maintenance.json").exists()
        assert not list(host.paths.releases.glob(".staging-*"))
        assert not list((host.paths.state / "compose").glob("*-production.json"))


def test_unknown_database_head_is_rejected_before_candidate_or_maintenance(host):
    host.runner.snapshot_database_head = "0039_unknown"
    preserved = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            host.paths.etc / "host.env",
            host.paths.etc / "release-public-key.pem",
            host.paths.var / "data/attachment",
        )
    }

    result = apply_release(host.request(), host.paths, host.runner)

    assert result.error == "migration_head_mismatch"
    assert not (host.paths.state / "updater-journal.json").exists()
    assert not (host.paths.state / "maintenance.json").exists()
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest
        for path, digest in preserved.items()
    )
    assert host.paths.current.resolve().name == "1.0.0"


def test_database_preflight_failure_is_reported_without_secret_or_mutation(host):
    host.runner.fail_on = "psql"
    result = apply_release(host.request(), host.paths, host.runner)
    assert result.state == "rejected"
    assert result.error == "migration_head_mismatch"
    assert not (host.paths.state / "updater-journal.json").exists()
    assert host.paths.current.resolve().name == "1.0.0"


def test_durable_snapshot_exists_before_writers_are_quiesced(host, monkeypatch):
    original = updater.atomic_write_json

    def interrupt(path, payload, *args, **kwargs):
        original(path, payload, *args, **kwargs)
        if path.name == "updater-journal.json" and payload.get("phase") == "maintenance":
            raise SimulatedPowerLoss()

    monkeypatch.setattr(updater, "atomic_write_json", interrupt)
    with pytest.raises(SimulatedPowerLoss):
        apply_release(host.request(), host.paths, host.runner)
    journal = json.loads((host.paths.state / "updater-journal.json").read_text())
    snapshot_root = host.paths.ops / "rollbacks" / journal["job_id"]
    assert journal["snapshot_done"] is True
    assert (snapshot_root / "data/attachment").read_bytes() == b"attachment"
    assert (snapshot_root / "database.dump").is_file()
    assert not (host.paths.state / "maintenance.json").exists()


@pytest.mark.parametrize("phase", ["snapshotted", "migrating"])
@pytest.mark.parametrize(
    "source_head",
    [
        "0036_audit_remediation_state",
        "0037_claim_workflow_visibility",
        "0038_inventory_photo_cleanup",
    ],
)
def test_rc5_named_state_survives_interrupted_update(host, monkeypatch, phase, source_head):
    rc5_archive = host.package(
        "0.2.0-rc.5", meta={"migration_head": source_head, "migration_compatibility": {}}
    )
    rc5_release = host.paths.releases / "0.2.0-rc.5"
    rc5_release.mkdir()
    with zipfile.ZipFile(rc5_archive) as archive:
        archive.extractall(rc5_release)
    host.paths.current.unlink()
    host.paths.current.symlink_to(rc5_release)
    host.runner.snapshot_database_head = source_head
    host.runner.database_heads = ["0038_inventory_photo_cleanup"]
    rc6_archive = host.package(
        "0.2.0-rc.6",
        meta={
            "migration_head": "0038_inventory_photo_cleanup",
            "migration_compatibility": {
                "from_heads": [
                    "0036_audit_remediation_state",
                    "0037_claim_workflow_visibility",
                    "0038_inventory_photo_cleanup",
                ],
                "reversible": True,
            },
        },
    )
    data = host.paths.var / "data"
    (data / "attachments").mkdir()
    (data / "attachments/report.bin").write_bytes(b"report attachment")
    (data / "backup-uuid").write_text("00000000-0000-4000-8000-000000000001")
    (host.paths.etc / "tuna.env").write_text("TUNA_TOKEN=retained")
    volume = host.paths.root / "var/lib/docker/volumes/robopark_robopark_postgres/_data"
    volume.mkdir(parents=True)
    (volume / "PG_VERSION").write_text("17")
    named = (
        host.paths.etc / "host.env",
        host.paths.etc / "tuna.env",
        host.paths.etc / "release-public-key.pem",
        data / "attachments/report.bin",
        data / "backup-uuid",
        volume / "PG_VERSION",
    )
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in named}
    original = updater.atomic_write_json

    def interrupt(path, payload, *args, **kwargs):
        original(path, payload, *args, **kwargs)
        if path.name == "updater-journal.json" and payload.get("phase") == phase:
            raise SimulatedPowerLoss()

    monkeypatch.setattr(updater, "atomic_write_json", interrupt)
    with pytest.raises(SimulatedPowerLoss):
        apply_release(host.request(rc6_archive), host.paths, host.runner)
    monkeypatch.setattr(updater, "atomic_write_json", original)
    recover_interrupted_update(host.paths, host.runner)

    assert {path: hashlib.sha256(path.read_bytes()).digest() for path in named} == before
    assert not any(command[:3] == ["docker", "volume", "rm"] for command in host.runner.commands)
