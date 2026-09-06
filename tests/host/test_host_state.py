import json
import os
import stat
from pathlib import Path

import pytest

from robopark_host.cli import main
from robopark_host.paths import HostPaths, paths_from_environment
from robopark_host.redaction import redact
from robopark_host.state import atomic_write_json, exclusive_lock


def test_fake_root_paths_are_immutable_and_follow_host_layout(host_paths):
    assert host_paths.root == Path(os.environ["ROBOPARK_ROOT"])
    assert host_paths.releases == host_paths.root / "opt/robopark/releases"
    assert host_paths.current == host_paths.root / "opt/robopark/current"
    assert host_paths.previous == host_paths.root / "opt/robopark/previous"
    assert host_paths.ops == host_paths.root / "var/lib/robopark/ops"
    assert host_paths.state == host_paths.root / "var/lib/robopark/ops/state"
    with pytest.raises(Exception):
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


@pytest.mark.parametrize(
    "command", ["status", "doctor", "repair", "update", "check-update", "watchdog"]
)
def test_cli_dispatches_each_host_command(command, tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOPARK_ROOT", str(tmp_path))
    monkeypatch.setenv("ROBOPARK_TESTING", "1")

    assert main([command]) == 0
