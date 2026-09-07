"""Root inbox consumer must claim once and export bounded public artifacts."""

import configparser
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport


def request(paths, kind="diagnostics", **changes):
    command = {
        "job_id": str(uuid4()),
        "kind": kind,
        "actor_user_id": 7,
        "created_at": datetime.now(UTC).isoformat(),
    }
    command.update(changes)
    inbox = paths.ops / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "approved.json").write_text(json.dumps(command))
    return command


def test_diagnostics_consumes_once_and_exports_readable_zip(host_paths, monkeypatch):
    from robopark_host import commands

    command = request(host_paths)
    monkeypatch.setattr(
        commands,
        "run_doctor",
        lambda *args: DiagnosticReport(
            [CheckResult("tuna_inactive", "failed", "Tuna unavailable", "restart_tuna")]
        ),
    )
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return CommandResult(stdout='{"MESSAGE":"secret-value","_CMDLINE":"secret-value"}')

    assert commands.consume_commands(host_paths, runner, object()) == 0
    assert not (host_paths.ops / "inbox/approved.json").exists()
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["job_id"] == command["job_id"]
    assert result["state"] == "succeeded"
    artifact = host_paths.ops / "public/artifacts" / result["artifact"]
    assert artifact.name == command["job_id"] + ".zip"
    assert artifact.stat().st_mode & 0o777 == 0o644
    with zipfile.ZipFile(artifact) as archive:
        assert "secret-value" not in "".join(archive.read(n).decode() for n in archive.namelist())
    before = len(calls)
    assert commands.consume_commands(host_paths, runner, object()) == 0
    assert len(calls) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "shell"},
        {"actor_user_id": 0},
        {"artifact": "/etc/passwd"},
        {"argv": ["rm", "-rf", "/"]},
        {"created_at": "2026-09-07T00:00:00"},
    ],
)
def test_consumer_rejects_noncanonical_command_without_running(host_paths, changes):
    from robopark_host import commands

    request(host_paths, **changes)
    assert (
        commands.consume_commands(
            host_paths, lambda *args, **kwargs: pytest.fail("invalid command executed"), object()
        )
        == 1
    )
    assert not (host_paths.ops / "inbox/approved.json").exists()


def test_repair_uses_fixed_allowlist_and_publishes_before_after(host_paths, monkeypatch):
    from robopark_host import commands

    request(host_paths, "repair")
    reports = iter(
        [
            DiagnosticReport(
                [
                    CheckResult("tuna_inactive", "failed", "failed", "restart_tuna"),
                    CheckResult("database_unavailable", "failed", "db"),
                ]
            ),
            DiagnosticReport(
                [
                    CheckResult("tuna_inactive", "ok", "ok"),
                    CheckResult("database_unavailable", "failed", "db"),
                ]
            ),
        ]
    )
    monkeypatch.setattr(commands, "run_doctor", lambda *args: next(reports))
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return CommandResult()

    commands.consume_commands(host_paths, runner, object())
    assert calls == [["systemctl", "restart", "robopark-tuna.service"]]
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["performed"] == ["restart_tuna"]
    assert result["before"][0]["status"] == "failed"
    assert result["after"][0]["status"] == "ok"


def test_path_trigger_runs_bounded_root_consumer():
    root = Path(__file__).resolve().parents[2] / "deploy/systemd"
    path = configparser.ConfigParser(interpolation=None)
    path.read(root / "robopark-commands.path")
    assert path["Path"]["PathExists"] == "/var/lib/robopark/ops/inbox/approved.json"
    service = configparser.ConfigParser(interpolation=None)
    service.read(root / path["Path"]["Unit"])
    assert (
        service["Service"]["ExecStart"]
        == "/usr/bin/python3 -I /opt/robopark/host-tools/robopark consume"
    )
    assert service["Service"].get("User", "root") == "root"
    assert int(service["Service"]["TimeoutStartSec"]) <= 18000
    assert service["Service"]["ProtectSystem"] == "strict"


def test_update_consumer_calls_launcher_once_without_doctor(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(host_paths, "update", artifact="update-test.zip")
    calls = []

    def launcher(paths, path, runner):
        calls.append(json.loads(path.read_text()))
        atomic_write_json(
            paths.ops / "public/rebuild.result",
            {"job_id": command["job_id"], "ok": True, "error": None},
            mode=0o644,
        )
        path.unlink()
        return 0

    monkeypatch.setattr("robopark_host.launcher.launch_update", launcher)
    monkeypatch.setattr(
        commands, "run_doctor", lambda *args: pytest.fail("update consumed as diagnostics")
    )
    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert calls == [command]


def test_repair_does_not_reexecute_claimed_command_after_crash(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(host_paths, "repair")
    atomic_write_json(host_paths.state / "command-request.json", command)
    monkeypatch.setattr(commands, "run_doctor", lambda *args: pytest.fail("repair replayed"))
    assert commands.consume_commands(host_paths, None, None) == 1
    # The API slot from a crash before unlink must also be retired.
    assert not (host_paths.ops / "inbox/approved.json").exists()
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["error"] == "command_interrupted"


def test_scheduled_doctor_publishes_allowlisted_readable_health(host_paths, monkeypatch):
    from robopark_host.doctor import run_doctor
    from test_diagnostic_review_regressions import ContractHttp, ReviewRunner

    report = run_doctor(host_paths, ReviewRunner("[]"), ContractHttp())
    path = host_paths.ops / "public/system-health.json"
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o644
    value = json.loads(path.read_text())
    assert value["generated_at"] == report.created_at
    assert set(value) == {
        "version",
        "git_sha",
        "generated_at",
        "overall",
        "checks",
        "update",
        "last_backup",
    }


def test_bundle_validates_allowed_metadata_values_not_only_field_names(host_paths, tmp_path):
    from robopark_host.bundle import create_diagnostic_bundle

    release = host_paths.releases / "1.0.0"
    release.mkdir(parents=True)
    (release / "manifest.json").write_text('{"app_version":"/root/LEAK","git_sha":"token=LEAK"}')
    host_paths.current.symlink_to(release)

    def runner(argv, **kwargs):
        if argv[0] == "journalctl":
            return CommandResult(
                stdout=json.dumps(
                    {
                        "__REALTIME_TIMESTAMP": "LEAK",
                        "PRIORITY": "LEAK",
                        "_SYSTEMD_UNIT": "LEAK",
                        "MESSAGE_ID": "token=LEAK",
                    }
                )
            )
        return CommandResult(stdout="[]")

    artifact = create_diagnostic_bundle(
        host_paths, DiagnosticReport([]), runner, tmp_path / "safe.zip"
    )
    with zipfile.ZipFile(artifact) as archive:
        assert "LEAK" not in "".join(archive.read(name).decode() for name in archive.namelist())


def test_installer_installs_command_trigger_as_part_of_atomic_unit_set(host_paths):
    import runpy

    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "deploy/installer/lib/install-services.py")
    )
    release = host_paths.releases / "1.0.0"
    units = release / "deploy/systemd"
    units.mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "deploy/systemd"
    for name in module["UNITS"]:
        (units / name).write_bytes((source / name).read_bytes())
    host_paths.current.symlink_to(release)
    module["install_units"](host_paths.root)
    assert (host_paths.root / "etc/systemd/system/robopark-commands.path").is_file()
    assert (host_paths.root / "etc/systemd/system/robopark-commands.service").is_file()


def test_completed_large_diagnostics_receipt_prevents_reexecution(host_paths, monkeypatch):
    from robopark_host import commands

    command = request(host_paths)
    report = DiagnosticReport([CheckResult(f"check{i}", "ok", "healthy" * 30) for i in range(26)])
    monkeypatch.setattr(commands, "run_doctor", lambda *args: report)
    assert commands.consume_commands(host_paths, lambda *args, **kwargs: CommandResult(), None) == 0
    (host_paths.ops / "inbox/approved.json").write_text(json.dumps(command))
    monkeypatch.setattr(
        commands, "run_doctor", lambda *args: pytest.fail("completed diagnostic replayed")
    )
    assert commands.consume_commands(host_paths, None, None) == 0


def test_bundle_drops_nested_compose_metadata(host_paths, tmp_path):
    from robopark_host.bundle import create_diagnostic_bundle

    def runner(argv, **kwargs):
        return CommandResult(
            stdout=json.dumps(
                {
                    "Service": "api",
                    "Name": {"cookie": "LEAK"},
                    "State": "/root/LEAK",
                    "Health": "LEAK",
                    "ExitCode": {"token": "LEAK"},
                }
            )
        )

    artifact = create_diagnostic_bundle(
        host_paths, DiagnosticReport([]), runner, tmp_path / "safe.zip"
    )
    with zipfile.ZipFile(artifact) as archive:
        assert "LEAK" not in archive.read("compose-services.json").decode()


def test_claimed_repair_after_a_day_fails_closed_without_replay(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(host_paths, "repair", created_at="2020-01-01T00:00:00+00:00")
    atomic_write_json(host_paths.state / "command-request.json", command)
    monkeypatch.setattr(commands, "run_doctor", lambda *args: pytest.fail("old repair replayed"))
    assert commands.consume_commands(host_paths, None, None) == 1
    assert not (host_paths.state / "command-request.json").exists()
    assert (
        json.loads((host_paths.ops / "public/command-result.json").read_text())["error"]
        == "command_interrupted"
    )


def test_command_service_retries_crashed_consumer_with_bounded_backoff():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(Path(__file__).resolve().parents[2] / "deploy/systemd/robopark-commands.service")
    assert parser["Service"]["Restart"] == "on-failure"
    assert 1 <= int(parser["Service"]["RestartSec"]) <= 30
    assert parser["Unit"]["StartLimitIntervalSec"] == "0"


def test_public_health_reports_backup_time_without_private_backup_fields(host_paths):
    from robopark_host.commands import publish_health
    from robopark_host.state import atomic_write_json

    atomic_write_json(
        host_paths.state / "last-backup.json",
        {
            "status": "success",
            "completed_at": "2026-09-07T00:00:00+00:00",
            "path": "/root/LEAK",
            "token": "LEAK",
        },
    )
    publish_health(host_paths, DiagnosticReport([]))
    raw = (host_paths.ops / "public/system-health.json").read_text()
    assert json.loads(raw)["last_backup"] == {
        "status": "success",
        "completed_at": "2026-09-07T00:00:00+00:00",
    }
    assert "LEAK" not in raw


def test_installer_enables_and_starts_approved_command_trigger(tmp_path):
    import subprocess

    script = Path(__file__).resolve().parents[2] / "deploy/installer/lib/services.sh"
    logfile = tmp_path / "systemctl.log"
    result = subprocess.run(
        [
            "sh",
            "-c",
            """
        . "$1"
        INSTALLER_DIR=/fixture
        ROBOPARK_OPT=/fixture
        ROBOPARK_ROOT=/fixture
        python3() { :; }
        curl() { return 0; }
        systemctl() { printf '%s\\n' "$*" >> "$TASK_LOG"; }
        die() { exit 1; }
        install_services
    """,
            "installer-test",
            str(script),
        ],
        env={"TASK_LOG": str(logfile)},
        capture_output=True,
    )
    assert result.returncode == 0
    commands = [line.split() for line in logfile.read_text().splitlines()]
    assert any(line[0] == "enable" and "robopark-commands.path" in line for line in commands)
    assert any(line[0] == "start" and "robopark-commands.path" in line for line in commands)


def test_expired_claimed_update_recovers_then_publishes_bounded_rejection(host_paths, monkeypatch):
    from types import SimpleNamespace

    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(
        host_paths, "update", artifact="update-approved.zip", created_at="2020-01-01T00:00:00+00:00"
    )
    atomic_write_json(host_paths.state / "command-request.json", command)
    recovered = []
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *args: recovered.append(True) or SimpleNamespace(state="idle"),
    )
    monkeypatch.setattr(
        "robopark_host.launcher.launch_update",
        lambda *args: pytest.fail("expired request executed"),
    )
    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 1
    assert recovered == [True]
    result = json.loads((host_paths.ops / "public/rebuild.result").read_text())
    assert result == {"job_id": command["job_id"], "ok": False, "error": "request_expired"}
    assert not (host_paths.state / "command-request.json").exists()
    assert not (host_paths.ops / "inbox/approved.json").exists()


def test_expired_approved_diagnostics_publishes_failure_without_work(host_paths, monkeypatch):
    from robopark_host import commands

    command = request(host_paths, "diagnostics", created_at="2020-01-01T00:00:00+00:00")
    monkeypatch.setattr(
        commands, "run_doctor", lambda *args: pytest.fail("expired request executed")
    )
    assert commands.consume_commands(host_paths, None, None) == 1
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["job_id"] == command["job_id"]
    assert result["state"] == "failed"
    assert result["error"] == "request_expired"
    assert not (host_paths.ops / "inbox/approved.json").exists()


def test_trigger_does_not_rate_limit_four_successful_commands(host_paths, monkeypatch):
    from robopark_host import commands

    unit = configparser.ConfigParser(interpolation=None)
    unit.read(Path(__file__).resolve().parents[2] / "deploy/systemd/robopark-commands.service")
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"
    monkeypatch.setattr(commands, "run_doctor", lambda *args: DiagnosticReport([]))
    for _ in range(4):
        command = request(host_paths)
        assert (
            commands.consume_commands(host_paths, lambda *args, **kwargs: CommandResult(), None)
            == 0
        )
        assert (
            json.loads((host_paths.ops / "public/command-result.json").read_text())["job_id"]
            == command["job_id"]
        )


def test_crash_retry_budget_is_per_command_and_survives_process_restarts(host_paths, monkeypatch):
    from types import SimpleNamespace

    from robopark_host import commands

    request(host_paths, "update", artifact="update-test.zip")
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *args: SimpleNamespace(state="idle"),
    )
    launches = []

    def crash(*args):
        launches.append(True)
        raise SystemExit("crash")

    monkeypatch.setattr("robopark_host.launcher.launch_update", crash)
    for _ in range(3):
        with pytest.raises(SystemExit):
            commands.consume_commands(host_paths, None, None, update_runner=object())
    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert len(launches) == 3
    assert json.loads((host_paths.ops / "public/maintenance.json").read_text())["enabled"] is True
    assert (
        json.loads((host_paths.ops / "public/host-status.json").read_text())["error"]
        == "manual_recovery_required"
    )


def test_deeply_nested_inbox_is_rejected_and_removed(host_paths, monkeypatch):
    from robopark_host import commands

    request(host_paths)
    (host_paths.ops / "inbox/approved.json").write_text("[" * 1500 + "0" + "]" * 1500)
    monkeypatch.setattr(
        commands.json, "loads", lambda *args, **kwargs: (_ for _ in ()).throw(RecursionError())
    )
    assert commands.consume_commands(host_paths, None, None) == 1
    assert not (host_paths.ops / "inbox/approved.json").exists()
