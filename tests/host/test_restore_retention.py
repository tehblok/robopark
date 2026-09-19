"""Only root-recorded terminal restore material may be reclaimed."""

import json
import os
import time
from uuid import uuid4

import pytest
from robopark_host.restore import _phase
from robopark_host.retention import artifact_usage, retain_artifacts


def material(paths, phase="succeeded"):
    identity = str(uuid4())
    request = {
        "kind": "restore",
        "job_id": identity,
        "artifact": f"restore-{identity}.zip",
        "sha256": "a" * 64,
        "actor_user_id": 1,
        "created_at": "2026-09-07T00:00:00+00:00",
    }
    journal = {
        "schema": 2,
        "request": request,
        "database_profile": "postgresql-17",
        "phase": phase,
        "snapshot_done": True,
        "writes_resumed": phase == "succeeded",
        "error": None,
        "publication_degraded": False,
    }
    _phase(paths, journal, phase)
    targets = [
        paths.state / "restores" / identity,
        paths.var / f".manual-displaced-{identity}",
        paths.var / f".manual-restore-{identity}",
    ]
    for target in targets:
        target.mkdir(parents=True, exist_ok=True)
        (target / "data").write_bytes(b"x" * 1000)
    upload = paths.ops / "artifacts" / request["artifact"]
    upload.parent.mkdir(parents=True, exist_ok=True)
    upload.write_bytes(b"x" * 1000)
    os.utime(upload, (time.time() - 40 * 86400,) * 2)
    return identity, targets, upload


def test_age_and_byte_retention_counts_real_restore_data_and_keeps_latest(host_paths):
    old_id, obsolete, upload = material(host_paths)
    current_id, current, current_upload = material(host_paths)
    assert artifact_usage(host_paths)["bytes"] >= 8000
    result = retain_artifacts(host_paths, now=time.time() + 40 * 86400, max_bytes=0)
    assert not result["blocked"]
    assert all(not path.exists() for path in [*obsolete, upload])
    assert all(path.exists() for path in [*current, current_upload])
    assert result["pressure"]  # protected current material is never sacrificed
    assert not (host_paths.state / "restore-owned" / f"{old_id}.json").exists()
    assert (host_paths.state / "restore-owned" / f"{current_id}.json").exists()


def test_pending_restore_and_unowned_recovery_are_preserved(host_paths):
    identity, pending, upload = material(host_paths)
    material(host_paths)
    request = {
        "kind": "restore",
        "job_id": identity,
        "artifact": upload.name,
        "sha256": "a" * 64,
        "actor_user_id": 1,
        "created_at": "2026-09-07T00:00:00+00:00",
    }
    (host_paths.ops / "inbox").mkdir()
    (host_paths.ops / "inbox/approved.json").write_text(json.dumps(request))
    foreign = host_paths.var / f".manual-displaced-{uuid4()}"
    foreign.mkdir()
    (foreign / "data").write_bytes(b"keep")
    retain_artifacts(host_paths, now=time.time() + 40 * 86400, max_bytes=0)
    assert all(path.exists() for path in [*pending, upload, foreign])


@pytest.mark.parametrize("corruption", ["symlink", "receipt", "journal"])
def test_uncertain_restore_state_blocks_cleanup(host_paths, tmp_path, corruption):
    identity, obsolete, upload = material(host_paths)
    material(host_paths)
    if corruption == "symlink":
        (obsolete[0] / "outside").symlink_to(tmp_path)
    elif corruption == "receipt":
        (host_paths.state / "restore-owned" / f"{identity}.json").write_text("{}")
    else:
        (host_paths.state / "restore-journal.json").write_text("{}")
    result = retain_artifacts(host_paths, now=time.time() + 40 * 86400, max_bytes=0)
    assert result["blocked"]
    assert all(path.exists() for path in [*obsolete, upload])


def test_invalid_pending_restore_prevents_deletion(host_paths):
    _, obsolete, upload = material(host_paths)
    material(host_paths)
    (host_paths.ops / "inbox").mkdir()
    (host_paths.ops / "inbox/approved.json").write_text('{"kind":"restore","job_id":"invalid"}')
    assert retain_artifacts(host_paths, now=time.time() + 40 * 86400, max_bytes=0)["blocked"]
    assert all(path.exists() for path in [*obsolete, upload])


def test_cleanup_interruption_keeps_ownership_until_all_trees_are_removed(host_paths, monkeypatch):
    import shutil

    identity, obsolete, upload = material(host_paths)
    material(host_paths)
    original = shutil.rmtree

    def fail(path, **kwargs):
        if path == obsolete[1].name:
            raise OSError("disk failure")
        return original(path, **kwargs)

    fail.avoids_symlink_attacks = original.avoids_symlink_attacks
    monkeypatch.setattr(shutil, "rmtree", fail)
    assert retain_artifacts(host_paths, now=time.time() + 40 * 86400, max_bytes=0)["blocked"]
    assert (host_paths.state / "restore-owned" / f"{identity}.json").exists()
    monkeypatch.setattr(shutil, "rmtree", original)
    assert not retain_artifacts(host_paths, now=time.time() + 40 * 86400, max_bytes=0)["blocked"]
    assert all(not path.exists() for path in [*obsolete, upload])


def test_expansion_reserves_root_storage_before_extracting(host_paths, monkeypatch, tmp_path):
    import hashlib
    from datetime import UTC, datetime

    from robopark_api.services.ops.archives import build_archive
    from robopark_host import retention
    from robopark_host.restore import run_restore
    from test_updater import host as host_factory

    host = host_factory.__wrapped__(host_paths)
    source = tmp_path / "source/data"
    source.mkdir(parents=True)
    (source / "robopark.db").write_bytes(b"not reached")
    blob = build_archive(kind="snapshot", source_root=source.parent, app_version="1.0.0")
    identity = str(uuid4())
    artifact = f"restore-{identity}.zip"
    (host_paths.ops / "artifacts" / artifact).write_bytes(blob)
    request = {
        "kind": "restore",
        "job_id": identity,
        "artifact": artifact,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "actor_user_id": 1,
        "created_at": datetime.now(UTC).isoformat(),
    }
    monkeypatch.setattr(retention, "MAX_BYTES", 1)
    assert run_restore(host_paths, request, host.runner)["state"] == "failed"
    assert not (host_paths.state / "restores" / identity / "candidate").exists()
    assert not (host_paths.state / "restores" / identity / "snapshot.zip").exists()


def test_interrupted_atomic_receipt_temporary_does_not_strand_restore(host_paths):
    identity, _, _ = material(host_paths)
    (host_paths.state / "restore-owned" / ("." + identity + ".json.tmp12345")).write_text("partial")
    assert not artifact_usage(host_paths)["blocked"]


def test_successful_scheduled_cleanup_clears_prior_restore_cleanup_failure(host_paths):
    from robopark_host.doctor import _artifact_check

    material(host_paths)
    (host_paths.state / "restore-retention.json").write_text('{"blocked":true}')
    assert _artifact_check(host_paths, None).status == "failed"
    assert not retain_artifacts(host_paths)["blocked"]
    assert _artifact_check(host_paths, None).status == "ok"
