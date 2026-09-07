import json
import os

from robopark_host.checks import CommandResult
from robopark_host.doctor import run_doctor, run_status
from robopark_host.watchdog import run_watchdog


class DoctorRunner:
    def __init__(self, db_ready=True):
        self.db_ready = db_ready
        self.calls = []

    def __call__(self, command, *, timeout, max_output):
        self.calls.append((command, timeout, max_output))
        if command == ["docker", "compose", "ps", "--format", "json"]:
            return CommandResult(stdout='[{"Service":"api","State":"running","Health":"healthy"}]')
        if command == ["docker", "compose", "exec", "-T", "api", "python", "-c", "from pathlib import Path; assert Path('/data/robopark.db').exists()"]:
            return CommandResult(returncode=0 if self.db_ready else 1)
        return CommandResult()


class ReadyHttp:
    def __init__(self, db_ready=True):
        self.db_ready = db_ready

    def get(self, url, *, timeout):
        return type(
            "Response",
            (),
            {
                "status": 200,
                "headers": {"X-Content-Type-Options": "nosniff"},
                "json": lambda response: {
                    "status": "ready" if self.db_ready else "degraded",
                    "checks": {
                        "database": "ok" if self.db_ready else "error",
                        "integrations": "ok",
                    },
                },
            },
        )()


class UnreadyHttp:
    def get(self, url, *, timeout):
        raise OSError("connection refused")


def test_database_failure_is_reported_but_never_auto_repaired(host_paths):
    report = run_doctor(host_paths, DoctorRunner(), ReadyHttp(db_ready=False))

    item = report.by_code("database_unavailable")

    assert item.status == "failed"
    assert item.repair is None


def test_doctor_uses_bounded_argument_array_commands_and_never_exposes_env_values(host_paths):
    host_paths.etc.mkdir(parents=True)
    host_env = host_paths.etc / "host.env"
    host_env.write_text("SECRET_KEY=do-not-show\nTUNA_TOKEN=also-secret\n")
    os.chmod(host_env, 0o600)
    runner = DoctorRunner()

    report = run_doctor(host_paths, runner, ReadyHttp())

    assert all(isinstance(command, list) for command, _, _ in runner.calls)
    assert all(timeout > 0 and max_output <= 16_384 for _, timeout, max_output in runner.calls)
    serialized = json.dumps(report.as_dict(), ensure_ascii=False)
    assert "do-not-show" not in serialized
    assert "also-secret" not in serialized
    assert report.by_code("configuration").status in {"ok", "warning"}


def test_doctor_persists_a_bounded_secret_free_json_report_and_human_log(host_paths):
    run_doctor(host_paths, DoctorRunner(), ReadyHttp())

    report = host_paths.var / "diagnostics" / "latest.json"
    log = host_paths.root / "var/log/robopark/doctor.log"

    assert json.loads(report.read_text())["checks"]
    assert "Локальный веб-интерфейс" in log.read_text()
    assert len(log.read_bytes()) <= 16_384


def test_doctor_detects_cross_file_public_url_mismatch_without_exposing_either_value(host_paths):
    host_paths.etc.mkdir(parents=True)
    host_env = host_paths.etc / "host.env"
    tuna_env = host_paths.etc / "tuna.env"
    host_env.write_text("SECRET_KEY=hidden\nPUBLIC_SITE_ORIGIN=https://one.example\n")
    tuna_env.write_text("TUNA_TOKEN=hidden\nPUBLIC_URL=https://two.example\n")
    os.chmod(host_env, 0o600)
    os.chmod(tuna_env, 0o600)

    report = run_doctor(host_paths, DoctorRunner(), ReadyHttp())

    assert report.by_code("configuration").status == "failed"
    assert "one.example" not in json.dumps(report.as_dict())
    assert "two.example" not in json.dumps(report.as_dict())


def test_status_is_a_concise_secret_free_view_of_diagnostics(host_paths):
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "tuna.env").write_text("TUNA_TOKEN=hidden\n")

    status = run_status(host_paths, DoctorRunner(), ReadyHttp())

    assert set(status) >= {"services", "resources", "version", "url", "last_backup"}
    assert "hidden" not in json.dumps(status)


def test_watchdog_restarts_the_application_only_after_three_local_readiness_failures(host_paths):
    runner = DoctorRunner()
    http = UnreadyHttp()

    assert run_watchdog(host_paths, runner, http).restarted is None
    assert run_watchdog(host_paths, runner, http).restarted is None
    result = run_watchdog(host_paths, runner, http)

    assert result.restarted == "restart_app"
    assert [call[0] for call in runner.calls].count(["systemctl", "restart", "robopark.service"]) == 1


def test_watchdog_resets_consecutive_failures_after_local_readiness_recovers(host_paths):
    runner = DoctorRunner()

    run_watchdog(host_paths, runner, UnreadyHttp())
    run_watchdog(host_paths, runner, UnreadyHttp())
    result = run_watchdog(host_paths, runner, ReadyHttp())

    assert result.consecutive_failures == 0
    assert json.loads((host_paths.state / "watchdog.json").read_text())["consecutive_failures"] == 0
