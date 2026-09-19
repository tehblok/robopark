import json
import os
import sys
import time
import zipfile

import pytest
from robopark_host.bundle import create_diagnostic_bundle
from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport
from robopark_host.cli import _doctor_handler, _Http, _repair_handler, _system_runner
from robopark_host.doctor import run_doctor, run_status
from robopark_host.repair import RepairReport
from robopark_host.watchdog import run_watchdog


class Response:
    def __init__(self, status=200, headers=None, payload=None):
        self.status = status
        self.headers = headers or {"X-Content-Type-Options": "nosniff"}
        self._payload = payload or {"status": "ready", "checks": {"database": "ok", "integrations": "ok"}}

    def json(self):
        return self._payload


class ContractHttp:
    def __init__(self, api_status=200):
        self.api_status = api_status
        self.urls = []

    def get(self, url, *, timeout):
        self.urls.append(url)
        if url.endswith("/api/health/ready"):
            ready = self.api_status < 400
            return Response(
                status=self.api_status,
                payload={
                    "status": "ready" if ready else "degraded",
                    "checks": {
                        "database": "ok" if ready else "error",
                        "integrations": "ok" if ready else "degraded",
                    },
                },
            )
        return Response()


class ReviewRunner:
    def __init__(self, compose):
        self.compose = compose
        self.commands = []

    def __call__(self, command, *, timeout, max_output):
        self.commands.append(command)
        if "ps" in command:
            return CommandResult(stdout=self.compose)
        if command[:2] == ["timedatectl", "show"]:
            return CommandResult(stdout="no\n")
        if command[:2] == ["df", "-Pk"]:
            return CommandResult(stdout="Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/root 100 99 1 99% /\n")
        if command[:2] == ["df", "-Pi"]:
            return CommandResult(stdout="Filesystem Inodes IUsed IFree IUse% Mounted on\n/dev/root 100 99 1 99% /\n")
        return CommandResult()


def _release(host_paths):
    release = host_paths.releases / "v1"
    (release / "deploy").mkdir(parents=True)
    (release / "deploy" / "docker-compose.yml").write_text("services: {}\n")
    (release / "manifest.json").write_text(
        json.dumps({"app_version": "1.2.3", "format": 1, "migration_head": "head"})
    )
    host_paths.current.parent.mkdir(parents=True, exist_ok=True)
    host_paths.previous.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(release, host_paths.current)
    os.symlink(release, host_paths.previous)


def test_bundle_excludes_credentials_json_cli_cookie_payloads_and_compose_commands(host_paths, tmp_path):
    _release(host_paths)
    sentinel = "FAKE_SECRET"
    runner = ReviewRunner('{"Service":"api","State":"running","Command":"exec tuna --token FAKE_SECRET"}')

    def journal(command, *, timeout, max_output):
        if command[0] == "journalctl":
            return CommandResult(stdout='Authorization: Bearer FAKE_SECRET\n{"TUNA_TOKEN":"FAKE_SECRET"}\nCookie: session=FAKE_SECRET\nexec tuna --token FAKE_SECRET\nuser payload FAKE_SECRET')
        return runner(command, timeout=timeout, max_output=max_output)

    bundle = create_diagnostic_bundle(host_paths, DiagnosticReport([CheckResult("x", "ok", "ok")]), journal, tmp_path / "bundle.zip")

    with zipfile.ZipFile(bundle) as archive:
        exported = "\n".join(archive.read(name).decode() for name in archive.namelist())
    assert sentinel not in exported
    assert "Command" not in exported
    compose_command = next(command for command in runner.commands if "ps" in command)
    assert "--project-name" in compose_command
    assert "--file" in compose_command


@pytest.mark.parametrize(
    "payload",
    ["[]", '{"Service":"api","State":"running","Health":"healthy"}', '[{"Service":"api","State":"exited","Health":""}]'],
)
def test_doctor_fails_missing_or_nonrunning_compose_services_and_uses_explicit_release_config(host_paths, payload):
    _release(host_paths)
    runner = ReviewRunner(payload)

    report = run_doctor(host_paths, runner, ContractHttp())

    assert report.by_code("containers").status == "failed"
    compose_command = next(command for command in runner.commands if "ps" in command)
    assert "--project-name" in compose_command
    assert str(host_paths.state / "current-compose.json") in compose_command


def test_doctor_grades_clock_and_full_resource_measurements_as_failed(host_paths):
    _release(host_paths)

    report = run_doctor(host_paths, ReviewRunner("[]"), ContractHttp())

    assert report.by_code("clock_sync").status == "failed"
    assert report.by_code("resources").status == "failed"
    assert report.by_code("inode_space").status == "failed"


def test_doctor_requires_database_migration_probe_in_addition_to_api_readiness(host_paths):
    _release(host_paths)

    class MigrationRunner(ReviewRunner):
        def __call__(self, command, *, timeout, max_output):
            if command[-2:] == ["alembic", "current"]:
                return CommandResult(returncode=1)
            return super().__call__(command, timeout=timeout, max_output=max_output)

    report = run_doctor(host_paths, MigrationRunner("[]"), ContractHttp())

    assert report.by_code("database_unavailable").status == "failed"


def test_doctor_marks_unknown_temperature_and_excessive_load_nonhealthy(host_paths):
    _release(host_paths)

    class ResourceRunner(ReviewRunner):
        def __call__(self, command, *, timeout, max_output):
            if command[0] == "cat":
                return CommandResult(stdout="")
            if command[0] == "uptime":
                return CommandResult(stdout=" 10:00:00 up 1 day, load average: 99.00, 99.00, 99.00")
            return super().__call__(command, timeout=timeout, max_output=max_output)

    report = run_doctor(host_paths, ResourceRunner("[]"), ContractHttp())

    assert report.by_code("temperature").status == "warning"
    assert report.by_code("load").status == "failed"


def test_watchdog_requires_api_readiness_even_if_web_root_is_healthy(host_paths):
    runner = ReviewRunner("[]")
    http = ContractHttp(api_status=503)

    run_watchdog(host_paths, runner, http)
    run_watchdog(host_paths, runner, http)
    result = run_watchdog(host_paths, runner, http)

    assert result.restarted == "restart_app"
    assert http.urls.count("http://127.0.0.1:8080/api/health/ready") == 3


def test_doctor_cli_returns_nonzero_when_a_required_check_fails(host_paths, monkeypatch):
    monkeypatch.setattr(
        "robopark_host.cli.run_doctor",
        lambda paths, runner, http: DiagnosticReport([CheckResult("database_unavailable", "failed", "База недоступна")]),
    )
    monkeypatch.setattr("robopark_host.cli._print", lambda payload: None)

    assert _doctor_handler(host_paths) != 0


def test_repair_cli_returns_nonzero_when_an_allowlisted_action_fails(host_paths, monkeypatch):
    healthy = DiagnosticReport([CheckResult("local_endpoint", "ok", "ok")])
    monkeypatch.setattr("robopark_host.cli.run_doctor", lambda paths, runner, http: healthy)
    monkeypatch.setattr(
        "robopark_host.cli.run_repairs",
        lambda report, allowlist, runner: RepairReport(failed=["restart_app"]),
    )
    monkeypatch.setattr("robopark_host.cli._print", lambda payload: None)

    assert _repair_handler(host_paths) != 0


def test_production_runner_drains_large_process_output_but_retains_only_the_cap():
    result = _system_runner(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
        timeout=5,
        max_output=128,
    )

    assert result.returncode == 0
    assert len(result.stdout) == 128


def test_bundle_keeps_allowlisted_journal_metadata_without_messages_or_credentials(host_paths, tmp_path):
    _release(host_paths)

    def runner(command, *, timeout, max_output):
        if command[0] == "journalctl":
            return CommandResult(
                stdout=json.dumps(
                    {
                        "__REALTIME_TIMESTAMP": "1720000000000000",
                        "PRIORITY": "6",
                        "_SYSTEMD_UNIT": "robopark.service",
                        "MESSAGE_ID": "robopark.ready",
                        "MESSAGE": "Authorization: Bearer FAKE_SECRET",
                        "_CMDLINE": "tuna --token FAKE_SECRET",
                    }
                )
            )
        return ReviewRunner("[]")(command, timeout=timeout, max_output=max_output)

    bundle = create_diagnostic_bundle(host_paths, DiagnosticReport([]), runner, tmp_path / "bundle.zip")

    with zipfile.ZipFile(bundle) as archive:
        journal = json.loads(archive.read("journal/robopark.service.json"))
    assert journal["entries"] == [
        {
            "event_id": "robopark.ready",
            "priority": "6",
            "timestamp": "1720000000000000",
            "unit": "robopark.service",
        }
    ]
    assert "FAKE_SECRET" not in json.dumps(journal)
    assert "MESSAGE" not in json.dumps(journal)
    assert "CMDLINE" not in json.dumps(journal)


def test_production_http_parses_bounded_healthy_json(monkeypatch):
    class RawResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def read(self, amount):
            assert amount == 16_385
            return b'{"status":"ready","checks":{"database":"ok","integrations":"ok"}}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: RawResponse())

    response = _Http().get("http://127.0.0.1:8080/api/health/ready", timeout=5)

    assert response.json()["checks"]["database"] == "ok"


def test_production_http_rejects_json_when_body_exceeds_cap(monkeypatch):
    class RawResponse:
        status = 200
        headers = {}

        def read(self, amount):
            assert amount == 16_385
            return b'{"status":"ready","checks":{"database":"ok"}}' + b" " * 16_340 + b"x"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: RawResponse())

    with pytest.raises(ValueError, match="exceeds"):
        _Http().get("http://127.0.0.1:8080/api/health/ready", timeout=5).json()


def test_actual_release_manifest_and_migration_head_are_reported(host_paths):
    _release(host_paths)

    class MigrationRunner(ReviewRunner):
        def __call__(self, command, *, timeout, max_output):
            if command[-2:] == ["alembic", "current"]:
                return CommandResult(stdout="head (head)\n")
            return super().__call__(command, timeout=timeout, max_output=max_output)

    report = run_doctor(host_paths, MigrationRunner("[]"), ContractHttp())
    status = run_status(host_paths, MigrationRunner("[]"), ContractHttp())

    assert report.by_code("release_layout").status == "ok"
    assert report.by_code("database_unavailable").status == "ok"
    assert status["version"] == "1.2.3"


def test_swap_exhaustion_and_naive_backup_timestamp_are_nonhealthy(host_paths):
    _release(host_paths)
    host_paths.state.mkdir(parents=True)
    (host_paths.state / "last-backup.json").write_text(
        json.dumps({"status": "success", "completed_at": "2026-09-07T12:00:00"})
    )

    class SwapRunner(ReviewRunner):
        def __call__(self, command, *, timeout, max_output):
            if command == ["env", "LC_ALL=C", "free", "-m"]:
                return CommandResult(stdout="Mem: 4096 1000 1000 0 2000 2000\nSwap: 1000 1000 0\n")
            if command[-2:] == ["alembic", "current"]:
                return CommandResult(stdout="head (head)\n")
            return super().__call__(command, timeout=timeout, max_output=max_output)

    report = run_doctor(host_paths, SwapRunner("[]"), ContractHttp())

    assert report.by_code("memory_load_swap").status == "failed"
    assert report.by_code("backup").status == "warning"


def test_memory_check_uses_stable_locale_for_host_metrics():
    from robopark_host.doctor import _memory_check

    def runner(command, *, timeout, max_output):
        if command == ["env", "LC_ALL=C", "free", "-m"]:
            return CommandResult(stdout="Mem: 4096 1000 1000 0 2000 2000\nSwap: 1000 100 900\n")
        return CommandResult(stdout="Память: 4096 1000 1000 0 2000 2000\nПодкачка: 1000 100 900\n")

    assert _memory_check(runner).status == "ok"


def test_runner_timeout_terminates_descendants_holding_pipes_open():
    started = time.monotonic()
    result = _system_runner(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); time.sleep(10)",
        ],
        timeout=1,
        max_output=128,
    )

    assert result.returncode == 124
    assert time.monotonic() - started < 2


def test_runner_deadline_applies_when_parent_exits_but_child_keeps_pipes_open():
    started = time.monotonic()
    result = _system_runner(
        [
            sys.executable,
            "-c",
            "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])",
        ],
        timeout=1,
        max_output=128,
    )

    assert result.returncode == 124
    assert time.monotonic() - started < 2


def test_doctor_handles_null_readiness_checks_and_malformed_migration_manifest(host_paths):
    _release(host_paths)
    (host_paths.current / "manifest.json").write_text("[]")

    class NullChecksHttp(ContractHttp):
        def get(self, url, *, timeout):
            if url.endswith("/api/health/ready"):
                return Response(payload={"status": "ready", "checks": None})
            return super().get(url, timeout=timeout)

    report = run_doctor(host_paths, ReviewRunner("[]"), NullChecksHttp())

    assert report.by_code("api_readiness").status == "failed"
    assert report.by_code("database_unavailable").status == "failed"


def test_healthy_installed_profile_does_not_require_legacy_ops_agent(host_paths):
    _release(host_paths)
    healthy = json.dumps([{'Service': name, 'State': 'running', 'Health': 'healthy'} for name in ('db', 'api', 'web')])
    report = run_doctor(host_paths, ReviewRunner(healthy), ContractHttp())
    assert report.by_code('containers').status == 'ok'
    assert report.by_code('containers').repair is None


def test_scheduled_doctor_actual_writes_fit_its_systemd_sandbox(host_paths, monkeypatch):
    import configparser
    from pathlib import Path

    unit = configparser.ConfigParser(interpolation=None)
    unit.read(Path(__file__).resolve().parents[2] / 'deploy/systemd/robopark-doctor.service')
    allowed = [host_paths.root / value.lstrip('/') for value in unit['Service']['ReadWritePaths'].split()]
    assert host_paths.var not in allowed
    _release(host_paths)
    real_replace = os.replace
    writes = []

    def enforce_sandbox(source, destination, *args, **kwargs):
        path = Path(destination)
        assert any(path.is_relative_to(directory) for directory in allowed), f'doctor sandbox denies {path}'
        writes.append(path)
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, 'replace', enforce_sandbox)
    run_doctor(host_paths, ReviewRunner('[]'), ContractHttp())
    assert host_paths.var / 'diagnostics/latest.json' in writes
    assert host_paths.root / 'var/log/robopark/doctor.log' in writes
