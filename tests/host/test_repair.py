import os

from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport
from robopark_host.repair import DEFAULT_REPAIRS, cleanup_retained, run_repairs


class RepairRunner:
    def __init__(self):
        self.commands = []

    def __call__(self, command, *, timeout, max_output):
        self.commands.append(command)
        return CommandResult()


def test_repair_restarts_only_allowlisted_failed_service():
    runner = RepairRunner()
    report = DiagnosticReport([CheckResult("tuna_inactive", "failed", "Tuna остановлен", "restart_tuna")])

    result = run_repairs(report, DEFAULT_REPAIRS, runner)

    assert result.performed == ["restart_tuna"]
    assert runner.commands == [["systemctl", "restart", "robopark-tuna.service"]]


def test_repair_leaves_database_and_unknown_repairs_untouched():
    runner = RepairRunner()
    report = DiagnosticReport(
        [
            CheckResult("database_unavailable", "failed", "База недоступна", None),
            CheckResult("unsafe", "failed", "Небезопасно", "drop_database"),
        ]
    )

    result = run_repairs(report, {**DEFAULT_REPAIRS, "drop_database": ["sqlite3", "/var/lib/robopark/data.db", ".dump"]}, runner)

    assert result.performed == []
    assert result.skipped == ["database_unavailable", "unsafe"]
    assert runner.commands == []


def test_repair_rejects_volume_pruning_even_when_the_caller_supplies_it():
    runner = RepairRunner()
    report = DiagnosticReport([CheckResult("space", "failed", "Нет места", "prune_volumes")])

    result = run_repairs(report, {"prune_volumes": ["docker", "system", "prune", "--volumes", "-f"]}, runner)

    assert result.performed == []
    assert result.skipped == ["space"]
    assert runner.commands == []


def test_cleanup_removes_only_expired_diagnostic_and_staging_artifacts(host_paths):
    staging = host_paths.ops / "staging"
    diagnostics = host_paths.var / "diagnostics"
    staging.mkdir(parents=True)
    diagnostics.mkdir(parents=True)
    expired = staging / "expired"
    fresh = diagnostics / "fresh.json"
    expired.mkdir()
    (expired / "candidate.txt").write_text("staging")
    fresh.write_text("keep")
    old_timestamp = 1_000.0
    os.utime(expired, (old_timestamp, old_timestamp))
    os.utime(fresh, (9_999.0, 9_999.0))

    deleted = cleanup_retained(host_paths, now=10_000.0, retention_seconds=500)

    assert deleted == [expired]
    assert not expired.exists()
    assert fresh.exists()
