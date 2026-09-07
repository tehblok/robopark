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
    target = host_paths.ops / "host.lock"

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
    "command", ["status", "doctor", "repair", "update", "check-update", "watchdog"]
)
def test_cli_dispatches_each_host_command(command, tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOPARK_ROOT", str(tmp_path))
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    monkeypatch.setitem(COMMAND_HANDLERS, command, lambda paths: 0)

    assert main([command]) == 0
