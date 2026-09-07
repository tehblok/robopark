"""Independent service entrypoints must not reopen writers during a transaction."""

import json

import pytest
from robopark_host import cli
from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport
from robopark_host.state import exclusive_lock
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
        with exclusive_lock(host_paths.ops / "host.lock"):
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
        with exclusive_lock(host_paths.ops / "host.lock"):
            result = run_watchdog(host_paths, runner, UnreadyHttp())
    assert result.restarted is None
    assert calls == []
    assert json.loads(state.read_text())["consecutive_failures"] == 2
