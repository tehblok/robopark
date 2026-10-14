import configparser
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/host"))

from robopark_host import network_recovery as recovery

INTERFACE = "wlP1p1s0"
CONNECTION_UUID = "e5cb7e24-9a0c-46d3-a159-2f062ea87f10"


def write_config(path: Path, **overrides) -> Path:
    value = {
        "interface": INTERFACE,
        "connection_uuid": CONNECTION_UUID,
        "enabled": True,
    }
    value.update(overrides)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize("value", [None, {"enabled": False}])
def test_missing_or_disabled_config_skips_without_subprocess(
    tmp_path, monkeypatch, capsys, value
):
    config_path = tmp_path / "network-recovery.json"
    if value is not None:
        config_path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("must not run subprocesses"),
    )

    assert recovery.run(config_path=config_path, runtime_dir=tmp_path / "run") == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "skipped"
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"interface": "wlan0;reboot"},
        {"connection_uuid": "not-a-uuid"},
        {"enabled": "yes"},
    ],
)
def test_invalid_config_is_rejected_before_subprocess(
    tmp_path, monkeypatch, capsys, overrides
):
    config_path = write_config(tmp_path / "network-recovery.json", **overrides)
    monkeypatch.setattr(
        recovery.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("must not run subprocesses"),
    )

    assert recovery.run(config_path=config_path, runtime_dir=tmp_path / "run") == 0
    assert json.loads(capsys.readouterr().out) == {
        "reason": "config_invalid",
        "state": "skipped",
    }


def test_probe_requires_ipv4_and_accepts_http_error_response(monkeypatch):
    commands = []

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        code = "404" if len(commands) == 1 else "000"
        return subprocess.CompletedProcess(argv, 0, stdout=code)

    monkeypatch.setattr(recovery.subprocess, "run", fake_run)
    config = recovery.RecoveryConfig(INTERFACE, CONNECTION_UUID)

    healthy, codes = recovery.check_connectivity(config)

    assert healthy is True
    assert codes == ["404", "000"]
    assert len(commands) == 2
    assert all("--ipv4" in command for command in commands)
    assert all(
        command[command.index("--interface") + 1] == INTERFACE
        for command in commands
    )


def test_check_only_does_not_lock_write_state_or_query_networkmanager(
    tmp_path, monkeypatch, capsys
):
    config_path = write_config(tmp_path / "network-recovery.json")
    monkeypatch.setattr(
        recovery, "check_connectivity", lambda _config: (True, ["200", "000"])
    )
    monkeypatch.setattr(
        recovery.fcntl,
        "flock",
        lambda *_args: pytest.fail("must not lock or create state"),
    )
    monkeypatch.setattr(
        recovery,
        "connection_guard",
        lambda _config: pytest.fail("must not query NetworkManager"),
    )

    assert (
        recovery.run(
            check_only=True,
            config_path=config_path,
            runtime_dir=tmp_path / "run",
        )
        == 0
    )
    assert not (tmp_path / "run").exists()
    assert json.loads(capsys.readouterr().out)["mode"] == "check-only"


def test_healthy_probe_resets_failures_but_preserves_cooldown():
    state, should_recover = recovery.next_state(
        {"failures": 4, "next_allowed_monotonic": 280.0},
        healthy=True,
        now=200.0,
    )

    assert state == {"failures": 0, "next_allowed_monotonic": 280.0}
    assert should_recover is False


def test_lock_precedes_probe_and_cooldown_precedes_recovery(
    tmp_path, monkeypatch, capsys
):
    config_path = write_config(tmp_path / "network-recovery.json")
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir()
    (runtime_dir / "recovery.json").write_text(
        '{"failures":2,"next_allowed_monotonic":0}\n', encoding="utf-8"
    )
    locked = False

    def lock(_file, _operation):
        nonlocal locked
        locked = True

    def probe(_config):
        assert locked is True
        return False, ["000", "000"]

    def recover(_config):
        state = json.loads((runtime_dir / "recovery.json").read_text())
        assert state["next_allowed_monotonic"] == 280.0
        return 0, 0

    monkeypatch.setattr(recovery.fcntl, "flock", lock)
    monkeypatch.setattr(recovery, "check_connectivity", probe)
    monkeypatch.setattr(recovery, "connection_guard", lambda _config: None)
    monkeypatch.setattr(recovery, "recover_connection", recover)
    monkeypatch.setattr(recovery.time, "monotonic", lambda: 100.0)

    assert recovery.run(config_path=config_path, runtime_dir=runtime_dir) == 0
    assert json.loads(capsys.readouterr().out)["recovery"] == "attempted"
    assert (runtime_dir / "recovery.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("guard_reason", ["wifi_radio_off", "active_wifi_changed"])
def test_radio_off_or_replacement_connection_is_never_disconnected(
    tmp_path, monkeypatch, capsys, guard_reason
):
    config_path = write_config(tmp_path / "network-recovery.json")
    runtime_dir = tmp_path / "run"
    runtime_dir.mkdir()
    (runtime_dir / "recovery.json").write_text(
        '{"failures":2,"next_allowed_monotonic":0}\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        recovery, "check_connectivity", lambda _config: (False, ["000", "000"])
    )
    monkeypatch.setattr(recovery, "connection_guard", lambda _config: guard_reason)
    monkeypatch.setattr(
        recovery,
        "recover_connection",
        lambda _config: pytest.fail("must not alter NetworkManager"),
    )
    monkeypatch.setattr(recovery.time, "monotonic", lambda: 100.0)

    assert recovery.run(config_path=config_path, runtime_dir=runtime_dir) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == guard_reason


def test_connection_guard_uses_radio_and_active_uuid_fields(monkeypatch):
    config = recovery.RecoveryConfig(INTERFACE, CONNECTION_UUID)
    commands = []
    outputs = iter(["enabled\n", f"{CONNECTION_UUID}:{INTERFACE}\n"])

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=next(outputs))

    monkeypatch.setattr(recovery.subprocess, "run", fake_run)

    assert recovery.connection_guard(config) is None
    assert commands == [
        ["nmcli", "--terse", "--fields", "WIFI", "general"],
        [
            "nmcli",
            "--terse",
            "--fields",
            "UUID,DEVICE",
            "connection",
            "show",
            "--active",
        ],
    ]


@pytest.mark.parametrize(
    "outputs,expected",
    [
        (["disabled\n"], "wifi_radio_off"),
        (
            ["enabled\n", f"11111111-1111-4111-8111-111111111111:{INTERFACE}\n"],
            "active_wifi_changed",
        ),
    ],
)
def test_connection_guard_protects_user_network_intent(monkeypatch, outputs, expected):
    config = recovery.RecoveryConfig(INTERFACE, CONNECTION_UUID)
    responses = iter(outputs)
    commands = []

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=next(responses))

    monkeypatch.setattr(recovery.subprocess, "run", fake_run)

    assert recovery.connection_guard(config) == expected
    assert len(commands) == len(outputs)


def test_recovery_uses_only_configured_interface_and_uuid(monkeypatch):
    config = recovery.RecoveryConfig(INTERFACE, CONNECTION_UUID)
    commands = []

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(recovery.subprocess, "run", fake_run)

    assert recovery.recover_connection(config) == (0, 0)
    assert commands == [
        ["nmcli", "--wait", "10", "device", "disconnect", INTERFACE],
        [
            "nmcli",
            "--wait",
            "45",
            "connection",
            "up",
            "uuid",
            CONNECTION_UUID,
            "ifname",
            INTERFACE,
        ],
    ]


def test_systemd_schedule_and_runtime_contract():
    service = configparser.ConfigParser(interpolation=None)
    service.optionxform = str
    service.read(ROOT / "deploy/systemd/robopark-network-recovery.service")
    timer = configparser.ConfigParser(interpolation=None)
    timer.optionxform = str
    timer.read(ROOT / "deploy/systemd/robopark-network-recovery.timer")

    assert service["Service"]["ExecStart"] == (
        "/usr/local/libexec/robopark-network-recovery.py"
    )
    assert service["Service"]["RuntimeDirectory"] == "robopark-network"
    assert service["Service"]["RuntimeDirectoryMode"] == "0700"
    assert service["Service"]["RuntimeDirectoryPreserve"] == "yes"
    assert service["Service"]["TimeoutStartSec"] == "100s"
    assert timer["Timer"]["OnBootSec"] == "120s"
    assert timer["Timer"]["OnUnitActiveSec"] == "60s"
    assert timer["Timer"]["Unit"] == "robopark-network-recovery.service"
