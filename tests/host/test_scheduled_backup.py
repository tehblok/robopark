"""Clean installs must schedule durable local snapshots."""

import configparser
import json
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path

from robopark_host import scheduled_backup
from robopark_host.operational_state import backup_state

ROOT = Path(__file__).resolve().parents[2]


def test_old_interrupted_partial_is_removed_without_touching_recent_or_foreign_files(
    tmp_path,
):
    directory = tmp_path / "backups"
    directory.mkdir(mode=0o700)
    abandoned = directory / ".robopark-abcdefgh.partial"
    abandoned.write_bytes(b"abandoned")
    old = time.time() - 3 * 3600
    os.utime(abandoned, (old, old))
    recent = directory / ".robopark-hijklmno.partial"
    recent.write_bytes(b"in progress")
    unrelated = directory / ".manual-abcdefgh.partial"
    unrelated.write_bytes(b"manual")
    link = directory / ".robopark-qrstuvwx.partial"
    link.symlink_to(unrelated)

    scheduled_backup._discard_abandoned_partials(directory)

    assert not abandoned.exists()
    assert recent.read_bytes() == b"in progress"
    assert unrelated.read_bytes() == b"manual"
    assert link.is_symlink()


def test_backup_name_collision_preserves_existing_verified_copy(
    host_paths, monkeypatch
):
    destination = host_paths.root / "var/backups/robopark"
    destination.mkdir(parents=True, mode=0o700)
    existing = destination / "robopark-20260926T041500000000.zip"
    existing.write_bytes(b"previous verified copy")

    class FixedDatetime:
        @staticmethod
        def now(zone):
            assert zone == UTC
            return datetime(2026, 9, 26, 4, 15, tzinfo=UTC)

    monkeypatch.setattr(scheduled_backup, "datetime", FixedDatetime)
    commands = []
    monkeypatch.setattr(
        scheduled_backup, "_run", lambda command, **_: commands.append(command) or ""
    )

    try:
        scheduled_backup.create_scheduled_backup(host_paths)
    except ValueError as exc:
        assert str(exc) == "backup_name_collision"
    else:
        raise AssertionError("existing copy was silently replaced")
    assert existing.read_bytes() == b"previous verified copy"
    assert not commands


def test_local_rotation_respects_allocated_byte_budget_and_exact_names(tmp_path):
    directory = tmp_path / "backups"
    directory.mkdir(mode=0o700)
    owned = []
    for day in range(1, 5):
        item = directory / f"robopark-202609{day:02d}T041500000000.zip"
        item.write_bytes(b"x" * 120)
        owned.append(item)
    unrelated = directory / "robopark-manual.zip"
    unrelated.write_bytes(b"manual")
    link = directory / "robopark-20260905T041500000000.zip"
    link.symlink_to(unrelated)
    one_copy_bytes = owned[-1].stat().st_blocks * 512

    scheduled_backup._rotate_verified_backups(
        directory, keep=14, max_bytes=2 * one_copy_bytes
    )

    assert [item.exists() for item in owned] == [False, False, True, True]
    assert unrelated.read_bytes() == b"manual"
    assert link.is_symlink()


def test_clean_install_publishes_daily_backup_timer():
    installer = (ROOT / "deploy/installer/lib/install-services.py").read_text()
    publisher = (ROOT / "deploy/ota/robopark_ota/host_install.py").read_text()
    updater = (ROOT / "deploy/host/robopark_host/rollback.py").read_text()
    remover = (ROOT / "deploy/ota/robopark_ota/remove.py").read_text()
    assert '"robopark-backup.service"' in installer
    assert '"robopark-backup.timer"' in installer
    assert '"robopark-backup.timer"' in publisher
    assert '"robopark-backup.timer"' in updater
    assert '"robopark-backup.timer"' in remover

    timer = configparser.ConfigParser(interpolation=None)
    timer.read(ROOT / "deploy/systemd/robopark-backup.timer")
    assert timer["Timer"]["OnCalendar"] == "*-*-* 04:15:00"
    assert timer["Timer"]["Persistent"] == "true"
    service = configparser.ConfigParser(interpolation=None)
    service.read(ROOT / "deploy/systemd/robopark-backup.service")
    command = service["Service"]["ExecStart"]
    assert command.endswith("/opt/robopark/host-tools/robopark backup")
    assert service["Service"]["ProtectSystem"] == "strict"
    assert "/var/lib/robopark/ops/state" in service["Service"]["ReadWritePaths"].split()


def test_failed_copy_keeps_previous_backup_and_no_confirmation(host_paths, monkeypatch):
    destination = host_paths.root / "var/backups/robopark"
    destination.mkdir(parents=True, mode=0o700)
    old = destination / "robopark-20260925T041500000000.zip"
    old.write_bytes(b"old backup")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "exec" in command:
            return "/ops/artifacts/snapshot-12345678-1234-1234-1234-123456789abc.zip\n"
        raise RuntimeError("copy_failed")

    monkeypatch.setattr(scheduled_backup, "_run", run)
    try:
        scheduled_backup.create_scheduled_backup(host_paths, keep=1)
    except RuntimeError as exc:
        assert str(exc) == "copy_failed"
    else:
        raise AssertionError("copy failure accepted")
    assert old.read_bytes() == b"old backup"
    assert not list(destination.glob("*.partial"))
    assert not any("--ack" in command for command in calls)


def test_verified_copy_is_durable_before_ack_and_rotates_only_owned_backup(
    host_paths, monkeypatch
):
    destination = host_paths.root / "var/backups/robopark"
    destination.mkdir(parents=True, mode=0o700)
    old = destination / "robopark-20260925T041500000000.zip"
    old.write_bytes(b"old backup")
    unrelated = destination / "manual.zip"
    unrelated.write_bytes(b"manual")
    calls = []
    directory_synced = False
    real_fsync = os.fsync

    def track_fsync(fd):
        nonlocal directory_synced
        real_fsync(fd)
        directory_synced |= stat.S_ISDIR(os.fstat(fd).st_mode)

    def run(command, **kwargs):
        calls.append(command)
        if "cp" in command:
            import zipfile

            with zipfile.ZipFile(command[-1], "w") as archive:
                archive.writestr("manifest.json", json.dumps({"kind": "snapshot"}))
            os.chmod(command[-1], 0o644)  # Docker cp can copy source permissions.
            return ""
        if "--ack" in command:
            assert len(list(destination.glob("robopark-*.zip"))) == 1
            assert directory_synced
            return ""
        return "/ops/artifacts/snapshot-12345678-1234-1234-1234-123456789abc.zip\n"

    monkeypatch.setattr(scheduled_backup, "_run", run)
    monkeypatch.setattr(scheduled_backup.os, "fsync", track_fsync)
    final = scheduled_backup.create_scheduled_backup(host_paths, keep=1)
    assert final.is_file() and final.stat().st_mode & 0o777 == 0o600
    assert not old.exists() and unrelated.read_bytes() == b"manual"
    assert sum("--ack" in command for command in calls) == 1


def test_failed_ack_does_not_accumulate_verified_local_backups(host_paths, monkeypatch):
    destination = host_paths.root / "var/backups/robopark"
    sequence = iter(range(1, 4))
    snapshot_calls = []

    class MovingDatetime:
        @staticmethod
        def now(zone):
            assert zone == UTC
            return datetime(2026, 9, 28, 4, 15, 0, next(sequence), tzinfo=UTC)

    def run(command, **_kwargs):
        if "cp" in command:
            import zipfile

            with zipfile.ZipFile(command[-1], "w") as archive:
                archive.writestr("manifest.json", json.dumps({"kind": "snapshot"}))
            return ""
        if "--ack" in command:
            raise RuntimeError("ack_failed")
        snapshot_calls.append(command)
        return "/ops/artifacts/snapshot-12345678-1234-1234-1234-123456789abc.zip\n"

    monkeypatch.setattr(scheduled_backup, "datetime", MovingDatetime)
    monkeypatch.setattr(scheduled_backup, "_run", run)
    for _ in range(3):
        try:
            scheduled_backup.create_scheduled_backup(host_paths, keep=2)
        except RuntimeError as exc:
            assert str(exc) == "ack_failed"
        else:
            raise AssertionError("failed ack accepted")

    assert len(list(destination.glob("robopark-*.zip"))) == 1
    assert len(snapshot_calls) == 1


def test_next_backup_retries_verified_copy_ack_before_creating_another(host_paths, monkeypatch):
    sequence = iter(range(1, 3))
    snapshots = []
    acknowledgements = []

    class MovingDatetime:
        @staticmethod
        def now(zone):
            assert zone == UTC
            return datetime(2026, 9, 28, 4, 15, 0, next(sequence), tzinfo=UTC)

    def run(command, **_kwargs):
        if "cp" in command:
            import zipfile

            with zipfile.ZipFile(command[-1], "w") as archive:
                archive.writestr("manifest.json", json.dumps({"kind": "snapshot"}))
            return ""
        if "--ack" in command:
            acknowledgements.append(command)
            if len(acknowledgements) == 1:
                raise RuntimeError("ack_failed_once")
            return ""
        snapshots.append(command)
        return "/ops/artifacts/snapshot-12345678-1234-1234-1234-123456789abc.zip\n"

    monkeypatch.setattr(scheduled_backup, "datetime", MovingDatetime)
    monkeypatch.setattr(scheduled_backup, "_run", run)
    try:
        scheduled_backup.create_scheduled_backup(host_paths, keep=2)
    except RuntimeError as exc:
        assert str(exc) == "ack_failed_once"
    else:
        raise AssertionError("failed ack accepted")

    next_backup = scheduled_backup.create_scheduled_backup(host_paths, keep=2)

    assert next_backup.is_file()
    assert len(snapshots) == 2
    assert len(acknowledgements) == 3
    assert not (host_paths.state / scheduled_backup.PENDING_ACK).exists()


def test_backup_command_records_copy_failure_without_exposing_exception(
    host_paths, monkeypatch, capsys
):
    from robopark_host import cli

    def broken(*_args, **_kwargs):
        raise RuntimeError("sensitive subprocess output")

    monkeypatch.setattr(scheduled_backup, "create_scheduled_backup", broken)
    assert cli._backup_handler(host_paths) == 1
    assert backup_state(host_paths)["status"] == "failed"
    output = capsys.readouterr().out
    assert "scheduled_backup_failed" in output
    assert "sensitive" not in output


def test_backup_command_records_success_only_after_copy(host_paths, monkeypatch):
    from robopark_host import cli

    confirmed = host_paths.root / "var/backups/robopark/robopark-verified.zip"

    def copied(*_args, **_kwargs):
        assert backup_state(host_paths)["status"] == "unknown"
        return confirmed

    monkeypatch.setattr(scheduled_backup, "create_scheduled_backup", copied)
    assert cli._backup_handler(host_paths) == 0
    assert backup_state(host_paths)["status"] == "success"


def test_backup_command_fails_when_host_receipt_cannot_be_written(
    host_paths, monkeypatch, capsys
):
    from robopark_host import cli

    monkeypatch.setattr(
        scheduled_backup,
        "create_scheduled_backup",
        lambda *_args, **_kwargs: (
            host_paths.root / "var/backups/robopark/confirmed.zip"
        ),
    )
    monkeypatch.setattr(
        "robopark_host.operational_state.record_backup",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("private detail")),
    )
    assert cli._backup_handler(host_paths) == 1
    assert "backup_receipt_failed" in capsys.readouterr().out
