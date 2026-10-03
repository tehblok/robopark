import json
import os
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from robopark_host.cli import COMMAND_HANDLERS, main
from robopark_host.paths import paths_from_environment
from robopark_host.redaction import redact
from robopark_host.state import atomic_write_json, exclusive_lock


def test_fake_root_paths_are_immutable_and_follow_host_layout(host_paths):
    assert host_paths.root == Path(os.environ["ROBOPARK_ROOT"])
    assert host_paths.releases == host_paths.root / "opt/robopark/releases"
    assert host_paths.current == host_paths.root / "opt/robopark/current"
    assert host_paths.previous == host_paths.root / "opt/robopark/previous"
    assert host_paths.ops == host_paths.root / "var/lib/robopark/ops"
    assert host_paths.state == host_paths.root / "var/lib/robopark/ops/state"
    with pytest.raises(FrozenInstanceError):
        host_paths.root = Path("/")


def test_non_root_override_is_rejected_outside_test_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOPARK_ROOT", str(tmp_path))
    monkeypatch.delenv("ROBOPARK_TESTING", raising=False)

    with pytest.raises(ValueError, match="ROBOPARK_ROOT"):
        paths_from_environment()


def test_atomic_state_is_mode_600_and_valid_json(host_paths):
    atomic_write_json(host_paths.state / "status.json", {"state": "ready"})
    target = host_paths.state / "status.json"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"state": "ready"}


def test_atomic_state_replaces_existing_json(host_paths):
    target = host_paths.state / "status.json"
    atomic_write_json(target, {"state": "installing"})
    atomic_write_json(target, {"state": "ready"})

    assert json.loads(target.read_text()) == {"state": "ready"}
    assert not list(target.parent.glob(".status.json.*"))


def test_atomic_state_rejects_nonstandard_numbers_without_replacing_state(host_paths):
    target = host_paths.state / "status.json"
    atomic_write_json(target, {"state": "ready"})

    with pytest.raises(ValueError, match="Out of range float values"):
        atomic_write_json(target, {"temperature": float("nan")})

    assert json.loads(target.read_text()) == {"state": "ready"}
    assert not list(target.parent.glob(".status.json.*"))


def test_exclusive_lock_creates_private_lock_file(host_paths):
    target = host_paths.host_lock

    with exclusive_lock(target):
        assert target.exists()
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_redaction_removes_nested_secret_values():
    value = {
        "TUNA_TOKEN": "tt_secret",
        "nested": {"password": "hidden"},
        "state": "ok",
    }

    assert redact(value) == {
        "TUNA_TOKEN": "[REDACTED]",
        "nested": {"password": "[REDACTED]"},
        "state": "ok",
    }


def test_redaction_keeps_structure_for_lists_and_tuples():
    value = [{"api_key": "secret"}, ("ok", {"secret": "hidden"})]

    assert redact(value) == [{"api_key": "[REDACTED]"}, ("ok", {"secret": "[REDACTED]"})]


def test_redaction_normalizes_secret_key_separators_without_redacting_normal_keys():
    value = {
        "nested": [
            {"privateKey": "private", "accessKey": "access"},
            {"api.key": "api", "publicKey": "visible", "apiVersion": "v1"},
        ]
    }

    assert redact(value) == {
        "nested": [
            {"privateKey": "[REDACTED]", "accessKey": "[REDACTED]"},
            {"api.key": "[REDACTED]", "publicKey": "visible", "apiVersion": "v1"},
        ]
    }


@pytest.mark.parametrize(
    "command", ["status", "doctor", "repair", "watchdog"]
)
def test_cli_dispatches_each_host_command(command, tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOPARK_ROOT", str(tmp_path))
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setitem(COMMAND_HANDLERS, command, lambda paths: 0)

    assert main([command]) == 0


def test_recovery_key_cli_moves_encrypted_backup_between_hosts_without_printing_key(
    tmp_path, monkeypatch, capsys
):
    from robopark_host.commands import (
        create_encrypted_backup,
        restore_encrypted_backup,
        verify_encrypted_backup,
    )

    host_a = tmp_path / "host-a"
    host_b = tmp_path / "host-b"
    transfer = tmp_path / "recovery.key"
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(host_a))
    assert main(["recovery-key", "init"]) == 0
    key_a = (host_a / "etc/robopark/backup-recovery.key").read_bytes()
    source = tmp_path / "source"
    source.mkdir()
    (source / "database.dump").write_bytes(b"portable")
    artifact = tmp_path / "backup.rpb"
    create_encrypted_backup(
        source, artifact, recovery_key=key_a,
        app_version="0.2.0-rc.9", schema_version="0050_media_action_dependency",
    )

    assert main(["recovery-key", "export", "--output", str(transfer)]) == 0
    output = capsys.readouterr().out
    assert key_a.hex() not in output
    assert stat.S_IMODE(transfer.stat().st_mode) == 0o600

    monkeypatch.setenv("ROBOPARK_ROOT", str(host_b))
    assert main(["recovery-key", "import", "--input", str(transfer)]) == 0
    key_b = (host_b / "etc/robopark/backup-recovery.key").read_bytes()
    assert key_b == key_a
    receipt = verify_encrypted_backup(artifact, recovery_key=key_b)
    restored = tmp_path / "restored"
    restore_encrypted_backup(artifact, restored, recovery_key=key_b, verified=receipt)
    assert (restored / "database.dump").read_bytes() == b"portable"
    assert key_b.hex() not in capsys.readouterr().out


def test_recovery_key_import_requires_exact_confirmation_and_preserves_previous_key(
    tmp_path, monkeypatch
):
    host_a = tmp_path / "host-a"
    host_b = tmp_path / "host-b"
    transfer = tmp_path / "recovery.key"
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(host_a))
    assert main(["recovery-key", "init"]) == 0
    assert main(["recovery-key", "export", "--output", str(transfer)]) == 0
    imported = transfer.read_bytes()

    monkeypatch.setenv("ROBOPARK_ROOT", str(host_b))
    assert main(["recovery-key", "init"]) == 0
    previous = (host_b / "etc/robopark/backup-recovery.key").read_bytes()
    assert previous != imported
    assert main(["recovery-key", "import", "--input", str(transfer)]) == 2
    assert (host_b / "etc/robopark/backup-recovery.key").read_bytes() == previous
    assert main([
        "recovery-key", "import", "--input", str(transfer),
        "--confirmation", "REPLACE ROBOPARK RECOVERY KEY",
    ]) == 0
    assert (host_b / "etc/robopark/backup-recovery.key").read_bytes() == imported
    preserved = list((host_b / "etc/robopark").glob("backup-recovery.key.previous-*"))
    assert len(preserved) == 1
    assert preserved[0].read_bytes() == previous
    assert stat.S_IMODE(preserved[0].stat().st_mode) == 0o600


@pytest.mark.parametrize("unsafe", ["mode", "length", "symlink", "directory"])
def test_recovery_key_import_rejects_unsafe_source_without_changing_host_key(
    unsafe, tmp_path, monkeypatch
):
    host = tmp_path / "host"
    source = tmp_path / "incoming.key"
    real = tmp_path / "real.key"
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(host))
    assert main(["recovery-key", "init"]) == 0
    original = (host / "etc/robopark/backup-recovery.key").read_bytes()
    if unsafe == "directory":
        source.mkdir()
    elif unsafe == "symlink":
        real.write_bytes(b"n" * 32); real.chmod(0o600); source.symlink_to(real)
    else:
        source.write_bytes(b"n" * (31 if unsafe == "length" else 32))
        source.chmod(0o644 if unsafe == "mode" else 0o600)

    assert main([
        "recovery-key", "import", "--input", str(source),
        "--confirmation", "REPLACE ROBOPARK RECOVERY KEY",
    ]) == 2
    assert (host / "etc/robopark/backup-recovery.key").read_bytes() == original


def test_recovery_key_export_is_exclusive_and_does_not_replace_existing_file(
    tmp_path, monkeypatch
):
    host = tmp_path / "host"
    destination = tmp_path / "existing.key"
    destination.write_bytes(b"keep")
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(host))
    assert main(["recovery-key", "init"]) == 0

    assert main(["recovery-key", "export", "--output", str(destination)]) == 2
    assert destination.read_bytes() == b"keep"


def test_recovery_key_import_rejects_key_owned_by_another_user(
    tmp_path, monkeypatch
):
    from robopark_host import commands

    host = tmp_path / "host"
    source = tmp_path / "incoming.key"
    source.write_bytes(b"n" * 32)
    source.chmod(0o600)
    real_fstat = commands.os.fstat

    def foreign_fstat(descriptor):
        values = list(real_fstat(descriptor))
        values[stat.ST_UID] = 999_999
        return os.stat_result(values)

    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setenv("ROBOPARK_ROOT", str(host))
    monkeypatch.setattr(commands.os, "fstat", foreign_fstat)

    assert main(["recovery-key", "import", "--input", str(source)]) == 2
    assert not (host / "etc/robopark/backup-recovery.key").exists()
