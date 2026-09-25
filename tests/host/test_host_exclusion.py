"""Independent service entrypoints must not reopen writers during a transaction."""

import json
import shutil

import pytest
from robopark_host import cli
from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport
from robopark_host.state import HostBusy, exclusive_lock, host_operation
from robopark_host.watchdog import run_watchdog
from test_doctor import UnreadyHttp


@pytest.mark.parametrize("owner", ["lock", "maintenance"])
def test_standalone_repair_cannot_mutate_a_busy_host(host_paths, monkeypatch, owner):
    calls = []
    monkeypatch.setattr(
        cli, "_system_runner", lambda args, **kw: calls.append(args) or CommandResult()
    )
    monkeypatch.setattr(
        cli,
        "run_doctor",
        lambda *a: DiagnosticReport(
            [CheckResult("containers", "failed", "unavailable", "restart_app")]
        ),
    )
    if owner == "maintenance":
        host_paths.state.mkdir(parents=True)
        (host_paths.state / "maintenance.json").write_text("{}")
        assert cli._repair_handler(host_paths) == 75
    else:
        with exclusive_lock(host_paths.host_lock):
            assert cli._repair_handler(host_paths) == 75
    assert calls == []


@pytest.mark.parametrize("owner", ["lock", "maintenance"])
def test_watchdog_skips_busy_host_without_advancing_failure_counter(host_paths, owner):
    calls = []

    def runner(args, **kw):
        calls.append(args)
        return CommandResult()

    host_paths.state.mkdir(parents=True)
    state = host_paths.state / "watchdog.json"
    state.write_text(json.dumps({"consecutive_failures": 2}))
    if owner == "maintenance":
        (host_paths.state / "maintenance.json").write_text("{}")
        result = run_watchdog(host_paths, runner, UnreadyHttp())
    else:
        with exclusive_lock(host_paths.host_lock):
            result = run_watchdog(host_paths, runner, UnreadyHttp())
    assert result.restarted is None
    assert calls == []
    assert json.loads(state.read_text())["consecutive_failures"] == 2


def test_host_lock_survives_data_root_replacement_and_still_excludes_operations(host_paths):
    host_paths.var.mkdir(parents=True)
    with exclusive_lock(host_paths.host_lock):
        shutil.rmtree(host_paths.var)
        host_paths.state.mkdir(parents=True)
        with pytest.raises(HostBusy, match="host_busy"), host_operation(host_paths):
            pytest.fail("overlapping operation acquired a replaced in-tree lock")


def test_operation_progress_receipt_is_atomic_and_monotonic(host_paths):
    from uuid import uuid4

    from robopark_host.state import read_operation_progress, write_operation_progress

    identity = str(uuid4())
    write_operation_progress(host_paths, identity, "accepted", 0)
    write_operation_progress(host_paths, identity, "executing", 50)
    assert read_operation_progress(host_paths, identity) == {
        "operation_id": identity,
        "phase": "executing",
        "progress": 50,
        "schema": 1,
    }
    with pytest.raises(ValueError, match="progress_regression"):
        write_operation_progress(host_paths, identity, "accepted", 10)


def test_operation_progress_rejects_symlink_receipt(host_paths, tmp_path):
    from uuid import uuid4

    from robopark_host.state import read_operation_progress

    identity = str(uuid4())
    receipt = host_paths.state / "operation-progress" / f"{identity}.json"
    receipt.parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(
            {
                "schema": 1,
                "operation_id": identity,
                "phase": "succeeded",
                "progress": 100,
            }
        )
    )
    receipt.symlink_to(outside)
    with pytest.raises(ValueError, match="invalid_operation_progress"):
        read_operation_progress(host_paths, identity)
