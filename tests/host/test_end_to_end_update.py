"""Production installer -> signed approval -> launcher -> reboot -> diagnostics.

Only OS/process/network/Docker/systemd boundaries are simulated. This is not
proof that real Armbian images build or that systemd/Tuna survive a real reboot.
"""

import json
import os
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def e2e_host(monkeypatch, request):
    helper = Path(__file__).with_name("e2e_support.py")
    assert helper.exists(), "end-to-end host adapter is missing"
    from e2e_support import InstalledHost

    with InstalledHost(monkeypatch, interrupted="install_resume" in request.node.name) as host:
        yield host


def test_release_changes_dependencies_units_and_updater_then_survives_reboot(e2e_host):
    host = e2e_host
    before = host.installed_files()
    host.approve()
    assert host.result()["ok"] is True, host.result()
    assert host.version() == "0.1.1"
    assert not host.maintenance()
    assert host.data() == "migrated"
    assert host.installed_files() != before
    for name in host.changed_files:
        assert host.installed_files()[name] != before[name]
    assert host.launches[0].parent != host.launches[1].parent
    assert host.launches[1].parent == (host.paths.opt / "host-tools").resolve()
    host.reboot()
    assert host.version() == "0.1.1"
    assert host.command("doctor") == 0
    host.assert_boundaries()
    checks = json.loads((host.paths.var / "diagnostics/latest.json").read_text())["checks"]
    assert next(item for item in checks if item["code"] == "tuna_route")["status"] == "ok"


def test_signed_successor_source_executes_reconciliation_and_boot_recovery(e2e_host):
    host = e2e_host
    marker = host.paths.state / "e2e-successor.jsonl"
    host.approve()
    assert host.result()["ok"] is True, host.result()
    assert marker.exists(), "successor reconciled without executing its signed CLI source"
    records = [json.loads(line) for line in marker.read_text().splitlines()]
    assert [record["args"] for record in records] == [["update", "--reconcile"]]
    successor = (host.paths.opt / "host-tools/robopark_host/cli.py").resolve()
    assert records[0]["source"] == str(successor)
    assert records[0]["version"] == "0.1.1"

    marker.unlink()
    host.reboot()
    records = [json.loads(line) for line in marker.read_text().splitlines()]
    assert [record["args"] for record in records] == [["update", "--recover"]]
    assert records[0]["source"] == str(successor)
    assert records[0]["version"] == "0.1.1"


@pytest.mark.parametrize(
    "failure,error",
    [
        ("tamper", "signature_invalid"),
        ("build", "command_failed"),
        ("tests", "command_failed"),
        ("smoke", "smoke_failed"),
        ("migration", "command_failed"),
        ("local_health", "cutover_unhealthy"),
        ("disk", "insufficient_space"),
    ],
)
def test_rejected_or_failed_release_preserves_working_install(e2e_host, failure, error):
    host = e2e_host
    host.fail = failure
    host.approve()
    assert host.result()["ok"] is False
    assert host.result()["error"] == error
    host.fail = None
    host.reboot()
    assert host.version() == "0.1.0"
    assert host.data() == "original"
    host.assert_boundaries()
    assert not host.maintenance()
    assert host.command("doctor") == 0


@pytest.mark.parametrize("failure", ["public_health", "tuna"])
def test_public_outage_keeps_healthy_data_then_repair_recovers(e2e_host, failure):
    host = e2e_host
    host.fail = failure
    host.approve()
    assert host.version() == "0.1.1"
    assert host.result()["ok"] is True, host.result()
    assert (
        json.loads((host.paths.ops / "public/host-status.json").read_text())["publication"]
        == "degraded"
    )
    assert not host.maintenance()
    host.fail = None
    assert host.command("repair") == 0
    host.reboot()
    assert host.data() == "migrated"


def test_both_releases_broken_keep_snapshot_and_resume_when_previous_recovers(e2e_host):
    host = e2e_host
    host.fail = "both_health"
    host.approve()
    assert host.result()["error"] == "manual_recovery_required"
    assert host.maintenance()
    snapshots = list((host.paths.ops / "rollbacks").glob("*/data/robopark.db"))
    assert len(snapshots) == 1
    assert snapshots[0].read_text() == "original"
    host.fail = None
    host.reboot()
    assert host.version() == "0.1.0"
    assert host.data() == "original"
    host.assert_boundaries()
    assert not host.maintenance()


PHASES = [
    "unpacking",
    "unpacked",
    "building",
    "built",
    "testing",
    "tested",
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
    "rolling_back",
    "rollback_healthy",
    "rollback_resuming",
    "rolled_back",
]


@pytest.mark.parametrize("phase", PHASES)
def test_power_loss_at_durable_phase_recovers_through_boot_entrypoint(e2e_host, monkeypatch, phase):
    from e2e_support import PowerLoss

    host = e2e_host
    if phase.startswith("roll"):
        host.fail = "local_health"
    native_replace = os.replace
    tripped = False

    def replace(source, destination):
        nonlocal tripped
        native_replace(source, destination)
        if (
            Path(destination).name == "updater-journal.json"
            and not tripped
            and json.loads(Path(destination).read_text())["phase"] == phase
        ):
            tripped = True
            raise PowerLoss()

    # Power loss belongs to the durable filesystem boundary, not updater internals.
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(PowerLoss):
        host.approve()
    assert tripped
    monkeypatch.setattr(os, "replace", native_replace)
    host.fail = None
    host.reboot()
    assert host.version() in ("0.1.0", "0.1.1")
    assert host.data() == ("migrated" if host.version() == "0.1.1" else "original")
    assert not host.maintenance()
    assert host.command("doctor") == 0
    host.reboot()
    assert not host.maintenance()


def test_diagnostics_crosses_consumer_boundary_without_secrets(e2e_host):
    host = e2e_host
    host.submit("diagnostics")
    result = json.loads((host.paths.ops / "public/command-result.json").read_text())
    assert result["state"] == "succeeded"
    artifact = host.paths.ops / "public/artifacts" / result["artifact"]
    with zipfile.ZipFile(artifact) as archive:
        output = "\n".join(archive.read(name).decode() for name in archive.namelist())
    for secret in host.secrets:
        assert secret not in output
    assert "private-database-record" not in output
    assert artifact.stat().st_mode & 0o777 == 0o644
    host.assert_boundaries()


def test_github_discovery_requires_approval_and_outage_leaves_current_untouched(e2e_host):
    host = e2e_host
    host.discover(outage=True)
    assert host.available()["state"] == "discovery_stale"
    assert host.version() == "0.1.0"
    host.discover()
    assert host.available()["state"] == "available"
    assert host.version() == "0.1.0"
    host.submit("github-update", release_id=101)
    assert host.result()["ok"] is True, host.result()
    assert host.version() == "0.1.1"
    host.reboot()
    assert host.command("status") == 0


def test_install_resume_and_rerun_preserve_secrets_runtime_and_release(e2e_host):
    host = e2e_host
    config = (host.paths.state / "current-compose.json").read_bytes()
    secrets = [
        (host.paths.etc / name).read_bytes() for name in ("host.env", "tuna.env", "updater.env")
    ]
    host.installer.run_installer("--resume")
    host.installer.run_installer()
    assert (host.paths.state / "current-compose.json").read_bytes() == config
    assert [
        (host.paths.etc / name).read_bytes() for name in ("host.env", "tuna.env", "updater.env")
    ] == secrets
    assert host.version() == "0.1.0"


def test_strict_host_maintenance_blocks_four_process_database_writers(e2e_host):
    host = e2e_host
    host.check_multiworker = True
    host.approve()
    assert host.writer_outcomes == ["maintenance"] * 4
    assert host.result()["ok"] is True, host.result()


@pytest.mark.parametrize("failure", [None, "local_health"])
def test_cutover_restarts_inactive_tuna_after_requires_stop(e2e_host, failure):
    host = e2e_host
    host.fail = failure
    host.approve()
    assert host.version() == ("0.1.1" if failure is None else "0.1.0")
    assert not host.maintenance()
    assert host.app_active
    assert host.tuna_active, "Requires stop propagation left ingress inactive"


def test_failed_rollback_publication_preserves_restored_data_and_is_repairable(
    e2e_host, monkeypatch
):
    host = e2e_host
    host.fail = "local_health"
    original = host.run

    def outage(argv, **kwargs):
        if list(argv)[:3] == ["systemctl", "restart", "robopark-tuna.service"]:
            raise host.command_error("command_failed")
        return original(argv, **kwargs)

    monkeypatch.setattr(host, "run", outage)
    host.approve()
    assert host.version() == "0.1.0" and host.data() == "original"
    assert not host.maintenance()
    assert (
        json.loads((host.paths.ops / "public/host-status.json").read_text())["publication"]
        == "degraded"
    )
    host.fail = None
    monkeypatch.setattr(host, "run", original)
    assert host.command("repair") == 0
    assert host.tuna_active
