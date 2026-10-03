from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport
from robopark_host.repair import DEFAULT_REPAIRS, run_repairs


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
