"""Root inbox consumer must claim once and export bounded public artifacts."""

import configparser
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport


class FakeHostEffects:
    def __init__(self):
        self.calls = []

    def usb_format(self, operation_id, device):
        self.calls.append(("usb_format", operation_id, device.uuid, device.path))
        return {"device_uuid": device.uuid, "formatted": True}

    def reboot(self, operation_id):
        self.calls.append(("reboot", operation_id))
        return {"scheduled": True}

    def package_inspect(self, operation_id, package):
        self.calls.append(("package_inspect", operation_id, package))
        return {"package": package}


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


def typed_request(kind, **changes):
    operation_id = str(uuid4())
    value = {
        "job_id": operation_id,
        "kind": kind,
        "actor_user_id": 7,
        "created_at": datetime.now(UTC).isoformat(),
    }
    value.update(changes)
    return value


def authorization(command):
    return {
        "operation_id": command["job_id"],
        "operation_kind": command["kind"],
        "actor_user_id": command["actor_user_id"],
        "consumed": True,
        "validated_at": command["created_at"],
    }


def test_typed_schema_is_closed_and_has_no_execution_escape():
    from robopark_host.commands import OperationKind, validate_typed_operation
    from robopark_host.release import ReleaseError

    valid = typed_request(OperationKind.PACKAGE_INSPECT.value, package="openssl")
    assert validate_typed_operation(valid).kind is OperationKind.PACKAGE_INSPECT
    for injected in (
        {**valid, "argv": ["sh", "-c", "id"]},
        {**valid, "command": "id"},
        {**valid, "kind": "shell"},
        {**valid, "package": "openssl; id"},
    ):
        with pytest.raises(ReleaseError, match="invalid_command"):
            validate_typed_operation(injected)


def test_api_typed_union_and_bridge_revalidate_consumed_authorization(tmp_path):
    from pydantic import TypeAdapter, ValidationError
    from robopark_api.ops_schemas import HostOperationIn
    from robopark_api.services.ops import host_bridge

    identity = str(uuid4())
    payload = {
        "operation_id": identity,
        "kind": "reboot",
        "confirmation": "REBOOT ROBOPARK",
    }
    adapter = TypeAdapter(HostOperationIn)
    assert str(adapter.validate_python(payload).operation_id) == identity
    with pytest.raises(ValidationError):
        adapter.validate_python({**payload, "argv": ["reboot"]})

    ops = tmp_path / "api-ops"
    root = tmp_path / "host-ops"
    (root / "inbox").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "public").mkdir()
    (root / "public/command-claim.json").write_text(
        json.dumps(
            {
                "job_id": str(uuid4()),
                "kind": "diagnostics",
                "actor_user_id": 1,
                "active": False,
            }
        )
    )
    with pytest.raises(host_bridge.BridgeError, match="authorization_required"):
        host_bridge.enqueue_typed_operation(
            ops,
            root,
            payload,
            7,
            "session-hash",
            authorization_consumed=False,
        )
    consumed = {
        "operation_id": identity,
        "operation_kind": "reboot",
        "actor_user_id": 7,
        "consumed": True,
    }
    job = host_bridge.enqueue_typed_operation(
        ops,
        root,
        payload,
        7,
        "session-hash",
        authorization_consumed=consumed,
    )
    request_value = json.loads((root / "inbox/approved.json").read_text())
    assert job.id == identity == request_value["job_id"]
    assert request_value["authorization"] == {
        "operation_id": identity,
        "operation_kind": "reboot",
        "actor_user_id": 7,
        "consumed": True,
        "validated_at": job.created_at,
    }


def test_usb_format_requires_uuid_safe_removable_device_and_double_confirmation(
    host_paths,
):
    from robopark_host.commands import BlockDevice, execute_typed_operation
    from robopark_host.release import ReleaseError

    identity = "00000000-0000-4000-8000-000000000001"
    phrase = f"FORMAT USB {identity}"
    command = typed_request(
        "usb-format",
        device_uuid=identity,
        confirmation=phrase,
        confirmation_repeat=phrase,
    )
    command["authorization"] = authorization(command)
    effects = FakeHostEffects()
    devices = [BlockDevice(identity, "/dev/fake-usb", removable=True)]

    result = execute_typed_operation(host_paths, command, effects, devices=devices)
    assert result["state"] == "succeeded"
    assert effects.calls == [("usb_format", command["job_id"], identity, "/dev/fake-usb")]
    progress = host_paths.state / "operation-progress" / f"{command['job_id']}.json"
    progress.unlink()  # simulate a crash after the terminal receipt but before projection
    assert execute_typed_operation(host_paths, command, effects, devices=devices) == result
    assert json.loads(progress.read_text())["phase"] == "succeeded"
    assert len(effects.calls) == 1
    collision = typed_request("package-inspect", package="openssl")
    collision["job_id"] = command["job_id"]
    with pytest.raises(ReleaseError, match="duplicate_operation_id"):
        execute_typed_operation(host_paths, collision, effects)

    for unsafe in (
        BlockDevice(identity, "/dev/fake-system", removable=False),
        BlockDevice(identity, "/dev/fake-mounted", removable=True, mounted=True),
        BlockDevice(identity, "/dev/fake-root", removable=True, root_device=True),
        BlockDevice(identity, "/dev/fake-data", removable=True, data_device=True),
    ):
        other = {**command, "job_id": str(uuid4())}
        other["authorization"] = authorization(other)
        with pytest.raises(ReleaseError, match="unsafe_usb_device"):
            execute_typed_operation(host_paths, other, effects, devices=[unsafe])


def test_destructive_operation_revalidates_consumed_authorization_and_phrase(host_paths):
    from robopark_host.commands import execute_typed_operation
    from robopark_host.release import ReleaseError

    command = typed_request("reboot", confirmation="REBOOT ROBOPARK")
    effects = FakeHostEffects()
    with pytest.raises(ReleaseError, match="authorization_required"):
        execute_typed_operation(host_paths, command, effects)
    command["authorization"] = authorization(command)
    command["confirmation"] = "reboot robopark"
    with pytest.raises(ReleaseError, match="confirmation_required"):
        execute_typed_operation(host_paths, command, effects)
    assert effects.calls == []


def test_encrypted_backup_never_contains_key_and_restore_requires_external_key(tmp_path):
    from robopark_host.commands import (
        create_encrypted_backup,
        restore_encrypted_backup,
        verify_encrypted_backup,
    )
    from robopark_host.release import ReleaseError

    source = tmp_path / "source"
    source.mkdir()
    (source / "db.dump").write_bytes(b"private database")
    key = b"k" * 32
    artifact = tmp_path / "backup.rpb"
    created = create_encrypted_backup(
        source,
        artifact,
        recovery_key=key,
        app_version="0.2.0-rc.6",
        schema_version="0046_privileged_generation",
    )
    assert created["verified"] is False
    metadata = verify_encrypted_backup(artifact, recovery_key=key)
    raw = artifact.read_bytes()
    assert key not in raw and b"private database" not in raw
    target = tmp_path / "restored"
    with pytest.raises(ReleaseError, match="recovery_key_required"):
        restore_encrypted_backup(artifact, target, recovery_key=None, verified=metadata)
    restored = restore_encrypted_backup(artifact, target, recovery_key=key, verified=metadata)
    assert restored["verified"] is True
    assert (target / "db.dump").read_bytes() == b"private database"


def test_restore_rejects_unverified_or_incompatible_backup(tmp_path):
    from robopark_host.commands import (
        create_encrypted_backup,
        restore_encrypted_backup,
        verify_encrypted_backup,
    )
    from robopark_host.release import ReleaseError

    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"x")
    artifact = tmp_path / "backup.rpb"
    key = b"r" * 32
    create_encrypted_backup(
        source,
        artifact,
        recovery_key=key,
        app_version="0.2.0-rc.6",
        schema_version="0046_privileged_generation",
    )
    receipt = verify_encrypted_backup(artifact, recovery_key=key)
    with pytest.raises(ReleaseError, match="verified_backup_required"):
        restore_encrypted_backup(artifact, tmp_path / "no", recovery_key=key, verified=None)
    wrong_target = tmp_path / "wrong-key"
    with pytest.raises(ReleaseError, match="backup_integrity_failed"):
        restore_encrypted_backup(
            artifact,
            wrong_target,
            recovery_key=b"w" * 32,
            verified=receipt,
        )
    assert not wrong_target.exists()
    with pytest.raises(ReleaseError, match="backup_version_incompatible"):
        restore_encrypted_backup(
            artifact,
            tmp_path / "bad",
            recovery_key=key,
            verified={**receipt, "app_version": "9.0.0"},
        )


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


def test_fresh_update_is_not_superseded_by_previous_success(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    previous = str(uuid4())
    atomic_write_json(
        host_paths.state / "updater-journal.json",
        {"job_id": previous, "phase": "succeeded"},
    )
    atomic_write_json(
        host_paths.ops / "public/rebuild.result",
        {"job_id": previous, "ok": True, "error": None},
        mode=0o644,
    )
    command = request(host_paths, "update", artifact="update-next.zip")
    launched = []

    def launcher(paths, path, runner):
        launched.append(json.loads(path.read_text()))
        atomic_write_json(
            paths.ops / "public/rebuild.result",
            {"job_id": command["job_id"], "ok": True, "error": None},
            mode=0o644,
        )
        return 0

    monkeypatch.setattr("robopark_host.launcher.launch_update", launcher)

    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert launched == [command]


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
    tmpfiles = release / "deploy/tmpfiles.d"
    tmpfiles.mkdir(parents=True)
    (tmpfiles / "robopark.conf").write_bytes(
        (Path(__file__).resolve().parents[2] / "deploy/tmpfiles.d/robopark.conf").read_bytes()
    )
    host_paths.current.symlink_to(release)
    module["install_units"](host_paths.root)
    assert (host_paths.root / "etc/systemd/system/robopark-commands.path").is_file()
    assert (host_paths.root / "etc/systemd/system/robopark-commands.service").is_file()
    assert (host_paths.root / "etc/tmpfiles.d/robopark.conf").is_file()


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
        ROBOPARK_ETC="$TASK_ETC"
        mkdir -p "$ROBOPARK_ETC"
        printf "TUNA_SUBDOMAIN='park'\nTUNA_LOCATION='ru'\nTUNA_DOMAIN=''\n" > "$ROBOPARK_ETC/tuna.env"
        python3() { :; }
        curl() { return 0; }
        systemctl() { printf '%s\\n' "$*" >> "$TASK_LOG"; }
        die() { exit 1; }
        install_services
        start_host_automation
    """,
            "installer-test",
            str(script),
        ],
        env={"TASK_LOG": str(logfile), "TASK_ETC": str(tmp_path / "etc")},
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


def test_successful_newer_local_update_retires_superseded_host_request(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(
        host_paths,
        "update",
        artifact="update-old.zip",
        created_at=(datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
    )
    atomic_write_json(host_paths.state / "command-request.json", command)
    atomic_write_json(
        host_paths.ops / "public/command-claim.json",
        {**{key: command[key] for key in ("job_id", "kind", "actor_user_id")}, "active": True},
        mode=0o644,
    )
    successor = str(uuid4())
    candidate = host_paths.releases / ("0.1.35-" + successor)
    candidate.mkdir(parents=True)
    host_paths.current.symlink_to(candidate)
    atomic_write_json(
        host_paths.state / "updater-journal.json",
        {"job_id": successor, "candidate": candidate.name, "phase": "succeeded"},
    )
    atomic_write_json(
        host_paths.ops / "public/rebuild.result",
        {"job_id": successor, "ok": True, "error": None},
        mode=0o644,
    )
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *args: pytest.fail("superseded request entered update recovery"),
    )
    monkeypatch.setattr(
        "robopark_host.launcher.launch_update",
        lambda *args: pytest.fail("superseded update relaunched"),
    )

    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 1

    result = json.loads((host_paths.ops / "public/rebuild.result").read_text())
    assert result == {"job_id": command["job_id"], "ok": False, "error": "request_superseded"}
    assert not (host_paths.state / "command-request.json").exists()
    assert json.loads((host_paths.ops / "public/command-claim.json").read_text())["active"] is False


def test_resumed_update_after_previous_success_is_not_superseded(host_paths, monkeypatch):
    from types import SimpleNamespace

    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    previous = str(uuid4())
    candidate = host_paths.releases / ("0.1.33-" + previous)
    candidate.mkdir(parents=True)
    host_paths.current.symlink_to(candidate)
    journal_path = host_paths.state / "updater-journal.json"
    atomic_write_json(
        journal_path,
        {"job_id": previous, "candidate": candidate.name, "phase": "succeeded"},
    )
    atomic_write_json(
        host_paths.ops / "public/rebuild.result",
        {"job_id": previous, "ok": True, "error": None},
        mode=0o644,
    )
    command = request(
        host_paths,
        "update",
        artifact="update-next.zip",
        created_at=(
            datetime.fromtimestamp(host_paths.current.lstat().st_mtime, UTC) + timedelta(seconds=1)
        ).isoformat(),
    )
    atomic_write_json(host_paths.state / "command-request.json", command)
    # Reconciliation may rewrite the already-successful journal after this request.
    later = datetime.fromisoformat(command["created_at"]).timestamp() + 1
    os.utime(journal_path, (later, later))
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *args: SimpleNamespace(state="idle"),
    )

    def launcher(paths, path, runner):
        atomic_write_json(
            paths.ops / "public/rebuild.result",
            {"job_id": command["job_id"], "ok": True, "error": None},
            mode=0o644,
        )
        return 0

    monkeypatch.setattr("robopark_host.launcher.launch_update", launcher)

    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert json.loads((host_paths.ops / "public/rebuild.result").read_text()) == {
        "job_id": command["job_id"],
        "ok": True,
        "error": None,
    }
    assert not (host_paths.state / "command-request.json").exists()


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
