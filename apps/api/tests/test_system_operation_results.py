"""Owner operation history and downloads survive replacement of the latest job."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import login_as
from robopark_api.ops_schemas import public_result
from robopark_api.services.ops import host_bridge, operation_registry
from robopark_api.services.ops.jobs import new_job, save_job


def test_completed_operations_remain_in_actor_scoped_history(client, seed_royal, db_session):
    login_as(client, "royal", "secret")
    first = operation_registry.reserve(
        db_session,
        operation_id="11111111-1111-4111-8111-111111111111",
        actor_user_id=seed_royal.id,
        kind="package-inspect",
    )
    first.receipt_state = "terminal"
    first.state = "succeeded"
    first.host_result_json = json.dumps(
        {
            "package_result": {
                "package": "openssl",
                "installed": True,
                "version": "3.0.13-0ubuntu3.5",
            }
        }
    )
    other = operation_registry.reserve(
        db_session,
        operation_id="22222222-2222-4222-8222-222222222222",
        actor_user_id=seed_royal.id + 99,
        kind="diagnostics",
    )
    db_session.commit()
    response = client.get("/admin/ops/operations")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [first.operation_id]
    assert other.operation_id not in response.text
    assert (
        response.json()["items"][0]["host_result"]["package_result"]["version"]
        == "3.0.13-0ubuntu3.5"
    )


def test_result_projection_preserves_useful_fields_without_raw_host_output():
    value = public_result(
        {
            "kind": "package-inspect",
            "state": "succeeded",
            "detail": {
                "package": "openssl",
                "installed": True,
                "version": "3.0.13",
                "stdout": "private",
            },
        }
    ).model_dump(mode="json")
    assert value["package_result"] == {
        "package": "openssl",
        "installed": True,
        "version": "3.0.13",
        "updated": None,
    }
    assert "private" not in json.dumps(value)
    for kind in ("backup", "backup-verify"):
        result = public_result(
            {
                "kind": kind,
                "state": "succeeded",
                "detail": {
                    "backup_id": "11111111-1111-4111-8111-111111111111",
                    "verified": True,
                },
            }
        ).model_dump(mode="json")
        assert result["backup_result"]["verified"] is True
    assert (
        public_result(
            {
                "package_result": {
                    "package": "arbitrary",
                    "version": "3.0.13",
                    "installed": True,
                }
            }
        ).package_result
        is None
    )
    assert (
        public_result(
            {
                "package_result": {
                    "package": "openssl",
                    "version": "3.0.13\nprivate",
                    "installed": True,
                }
            }
        ).package_result
        is None
    )


def test_open_artifact_pins_file_and_rejects_nonterminal_receipt(tmp_path):
    directory = tmp_path / "public/artifacts"
    directory.mkdir(parents=True)
    identity = "11111111-1111-4111-8111-111111111111"
    target = directory / f"{identity}.zip"
    target.write_bytes(b"original")
    row = SimpleNamespace(
        kind="diagnostics", state="succeeded", receipt_state="terminal", operation_id=identity
    )
    with host_bridge.open_operation_artifact(tmp_path, row) as stream:
        target.rename(directory / "previous.zip")
        target.write_bytes(b"replacement")
        assert stream.read() == b"original"
    row.receipt_state = "accepted"
    assert host_bridge.open_operation_artifact(tmp_path, row) is None


def test_diagnostics_ready_requires_exact_generated_archive():
    value = {
        "kind": "diagnostics",
        "state": "succeeded",
        "job_id": "11111111-1111-4111-8111-111111111111",
        "detail": {"completed": True},
    }
    assert public_result(value).diagnostics_ready is False
    value["detail"]["artifact"] = value["job_id"] + ".zip"
    assert public_result(value).diagnostics_ready is True
    value["detail"]["artifact"] = "/etc/robopark/host.env"
    assert public_result(value).diagnostics_ready is False


def test_diagnostics_download_by_identity_survives_later_operation(
    client,
    seed_royal,
    db_session,
    test_settings,
    tmp_path,
):
    login_as(client, "royal", "secret")
    root = tmp_path / "host-ops"
    for name in ("inbox", "artifacts", "public/artifacts"):
        (root / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(root))
    identity = "11111111-1111-4111-8111-111111111111"
    row = operation_registry.reserve(
        db_session, operation_id=identity, actor_user_id=seed_royal.id, kind="diagnostics"
    )
    row.receipt_state, row.state = "terminal", "succeeded"
    db_session.commit()
    target = root / "public/artifacts" / f"{identity}.zip"
    target.write_bytes(b"checked diagnostic archive")
    response = client.get(f"/admin/ops/operations/{identity}/artifact")
    assert response.status_code == 200
    assert response.content == target.read_bytes()
    assert response.headers["cache-control"] == "no-store"
    target.unlink()
    target.symlink_to(Path(test_settings.ops_dir) / "private.json")
    assert client.get(f"/admin/ops/operations/{identity}/artifact").status_code == 404
    assert (
        client.get(
            "/admin/ops/operations/22222222-2222-4222-8222-222222222222/artifact"
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "root_actor_matches, expired", [(True, False), (False, False), (True, True)]
)
def test_restore_receipt_can_be_read_after_database_snapshot_removed_registry_row(
    client,
    seed_royal,
    db_session,
    test_settings,
    tmp_path,
    root_actor_matches,
    expired,
    monkeypatch,
):
    login_as(client, "royal", "secret")
    root = tmp_path / "host-ops"
    for name in ("inbox", "artifacts", "public"):
        (root / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(root))
    identity = "11111111-1111-4111-8111-111111111111"
    job = new_job("backup-restore", exempt_token_hash=None)
    job.id = identity
    job.state, job.phase = "running", "awaiting_host"
    if expired:
        job.state = "succeeded"
        job.created_at = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        monkeypatch.setattr("robopark_api.services.ops.jobs._now", lambda: job.created_at)
    job.extra = {
        "host_updater": True,
        "host_request": {
            "job_id": identity,
            "kind": job.kind,
            "actor_user_id": seed_royal.id,
            "created_at": job.created_at,
            "capability_revision": "a" * 64,
            "confirmation": "RESTORE ROBOPARK BACKUP",
            "backup_id": identity,
            "authorization": {
                "operation_id": identity,
                "operation_kind": job.kind,
                "actor_user_id": seed_royal.id,
                "consumed": True,
                "validated_at": job.created_at,
            },
        },
    }
    save_job(Path(test_settings.ops_dir), job)
    (root / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": identity,
                "kind": job.kind,
                "actor_user_id": seed_royal.id if root_actor_matches else seed_royal.id + 1,
                "state": "succeeded",
                "detail": {"backup_id": identity, "restored": True},
            }
        )
    )
    assert db_session.get(operation_registry.HostOperationStatus, identity) is None
    response = client.get(f"/admin/ops/operations/{identity}")
    assert response.status_code == (200 if root_actor_matches and not expired else 404)
    if not root_actor_matches or expired:
        return
    assert response.json()["receipt_state"] == "terminal"
    assert response.json()["host_result"]["backup_result"]["restored"] is True


def test_operation_context_is_boot_scoped_and_closed(
    client, seed_royal, test_settings, tmp_path, monkeypatch
):
    login_as(client, "royal", "secret")
    root = tmp_path / "host-ops"
    for name in ("inbox", "artifacts", "public"):
        (root / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(root))
    identity = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(host_bridge, "_host_boot_id", lambda: identity)
    value = {
        "schema": 1,
        "boot_id": identity,
        "generated_at": datetime.now(UTC).isoformat(),
        "valid_for_seconds": 300,
        "rollback_release": "0.2.0-rc.10",
        "selected_device_uuid": None,
        "packages": [
            "docker-ce",
            "docker-ce-cli",
            "containerd.io",
            "openssl",
            "python3-cryptography",
        ],
        "services": ["robopark.service"],
        "devices": [],
        "backups": [],
    }
    target = root / "public/operation-context.json"
    target.write_text(json.dumps(value))
    response = client.get("/admin/ops/operation-context")
    assert response.status_code == 200
    assert response.json()["services"] == ["robopark.service"]
    target.write_text(json.dumps({**value, "services": ["anything.service"]}))
    assert client.get("/admin/ops/operation-context").status_code == 503
    target.write_text(json.dumps({**value, "boot_id": "22222222-2222-4222-8222-222222222222"}))
    assert client.get("/admin/ops/operation-context").status_code == 503


@pytest.mark.parametrize(
    "reason",
    [
        "recovery_key_required",
        "usb_not_selected",
        "backup_not_verified",
        "package_not_installed",
        "manual_recovery_required",
    ],
)
def test_known_host_failure_retains_actionable_reason(tmp_path, reason):
    ops, root = tmp_path / "ops", tmp_path / "host"
    (root / "public").mkdir(parents=True)
    job = new_job("backup", exempt_token_hash=None)
    job.state = "running"
    job.extra = {"host_updater": True, "host_request": {"actor_user_id": 1}}
    save_job(ops, job)
    (root / "public/command-result.json").write_text(
        json.dumps(
            {
                "job_id": job.id,
                "actor_user_id": 1,
                "kind": "backup",
                "state": "failed",
                "detail": {},
                "error": reason,
            }
        )
    )
    assert host_bridge.reconcile_host_job(ops, root).error == reason


@pytest.mark.parametrize("route", ["artifact", "diagnostic-artifact"])
def test_legacy_latest_artifact_routes_cannot_bypass_operation_identity(client, seed_royal, route):
    login_as(client, "royal", "secret")
    response = client.get(f"/admin/ops/{route}")
    assert response.status_code == 410
    assert response.json()["detail"] == "typed_operation_required"
