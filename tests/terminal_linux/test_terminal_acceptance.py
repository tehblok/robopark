from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from scripts import terminal_acceptance


def fake_vm(tmp_path: Path, *, marker_mode: int = 0o600, init: str = "systemd"):
    marker = tmp_path / "run/robopark-terminal-test-vm"
    marker.parent.mkdir(parents=True)
    marker.write_text("disposable terminal acceptance VM\n")
    marker.chmod(marker_mode)
    proc = tmp_path / "proc/1"
    proc.mkdir(parents=True)
    (proc / "comm").write_text(f"{init}\n")


def test_acceptance_refuses_any_host_without_private_marker_and_systemd(tmp_path):
    with pytest.raises(RuntimeError, match="disposable_vm_marker_missing"):
        terminal_acceptance.require_disposable_vm(
            tmp_path, effective_uid=0, marker_owner_uid=os.getuid()
        )

    fake_vm(tmp_path, init="python")
    with pytest.raises(RuntimeError, match="systemd_pid1_required"):
        terminal_acceptance.require_disposable_vm(
            tmp_path, effective_uid=0, marker_owner_uid=os.getuid()
        )


def test_acceptance_requires_root_owned_0600_marker_and_root_runner(
    tmp_path, monkeypatch
):
    fake_vm(tmp_path, marker_mode=0o644)
    with pytest.raises(RuntimeError, match="disposable_vm_marker_unsafe"):
        terminal_acceptance.require_disposable_vm(
            tmp_path, effective_uid=0, marker_owner_uid=os.getuid()
        )

    (tmp_path / "run/robopark-terminal-test-vm").chmod(0o600)
    with pytest.raises(RuntimeError, match="linux_root_required"):
        terminal_acceptance.require_disposable_vm(
            tmp_path, effective_uid=1000, marker_owner_uid=os.getuid()
        )

    real_stat = Path.stat

    def wrong_owner(path, *args, **kwargs):
        value = real_stat(path, *args, **kwargs)
        if path.name == "robopark-terminal-test-vm":
            fields = list(value)
            fields[stat.ST_UID] = 1000
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(Path, "stat", wrong_owner)
    with pytest.raises(RuntimeError, match="disposable_vm_marker_unsafe"):
        terminal_acceptance.require_disposable_vm(tmp_path, effective_uid=0)


def test_acceptance_accepts_only_the_expected_disposable_fixture(tmp_path):
    fake_vm(tmp_path)
    info = terminal_acceptance.require_disposable_vm(
        tmp_path, effective_uid=0, marker_owner_uid=os.getuid()
    )
    assert info == {
        "init": "systemd",
        "marker_mode": "0600",
        "marker_uid": os.getuid(),
    }


def test_profile_descriptors_use_fixed_bounded_deadlines():
    capabilities = {
        "boot_id": "253c5c89-d915-46ab-8c34-fc8d73b14f72",
        "broker_epoch": "054fb28c-5b54-4716-9ebd-d429750f88fb",
        "capability_revision": "a" * 64,
    }
    maintenance = terminal_acceptance.session_request("maintenance", capabilities)
    root = terminal_acceptance.session_request("root", capabilities)

    assert maintenance["remaining_seconds"] == 3600
    assert root["remaining_seconds"] == 900
    assert maintenance["profile"] == "maintenance"
    assert root["profile"] == "root"
    assert maintenance["auth_session_hash"] == "b" * 64


def test_report_is_private_atomic_bounded_and_contains_no_terminal_payload(tmp_path):
    destination = tmp_path / "result.json"
    report = terminal_acceptance.new_report(
        platform={"init": "systemd", "marker_mode": "0600", "marker_uid": 0}
    )
    report["checks"].append(
        {
            "name": "maintenance_profile",
            "status": "PASS",
            "duration_seconds": 1.25,
            "observations": {"uid": 991, "cap_eff": "0000000000000000"},
        }
    )
    terminal_acceptance.write_report(destination, report)

    stored = json.loads(destination.read_text())
    assert stored["format"] == 1
    assert stored["checks"][0]["name"] == "maintenance_profile"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert destination.stat().st_size <= terminal_acceptance.REPORT_LIMIT

    report["checks"][0]["observations"]["transcript"] = "forbidden"
    with pytest.raises(ValueError, match="report_forbidden_field"):
        terminal_acceptance.write_report(destination, report)


def test_report_writer_rejects_symlink_and_does_not_replace_target(tmp_path):
    target = tmp_path / "target"
    target.write_text("unchanged")
    alias = tmp_path / "result.json"
    alias.symlink_to(target)

    with pytest.raises(ValueError, match="report_unsafe_path"):
        terminal_acceptance.write_report(alias, terminal_acceptance.new_report({}))
    assert target.read_text() == "unchanged"


def test_api_boundary_mode_contains_no_timer_or_direct_broker_checks():
    assert [name for name, _ in terminal_acceptance.selected_checks("api-boundary")] == [
        "api_container_to_host_broker"
    ]


def test_api_boundary_result_accepts_only_bounded_safe_observations():
    value = terminal_acceptance.parse_api_boundary_result(
        json.dumps(
            {
                "api_uid": 10001,
                "api_gid": 10001,
                "settings_socket": "/run/robopark-terminal/broker.sock",
                "host_root": "/host-ops",
                "projection_readable": True,
                "projection_fresh": True,
                "socket_present": True,
                "socket_connect": True,
                "capabilities": True,
                "maintenance_uid": 997,
                "maintenance_pty": True,
                "root_uid": 0,
                "root_pty": True,
                "cleanup": True,
            }
        )
    )
    assert value["maintenance_uid"] == 997
    assert value["root_uid"] == 0

    unsafe = dict(value, transcript="forbidden")
    with pytest.raises(RuntimeError, match="terminal_api_boundary_invalid"):
        terminal_acceptance.parse_api_boundary_result(json.dumps(unsafe))
