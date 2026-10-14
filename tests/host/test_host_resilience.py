import configparser
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NETWORK_UUID = "e5cb7e24-9a0c-46d3-a159-2f062ea87f10"
INTERFACE = "wlP1p1s0"
ACTIVE_WIFI = f"{NETWORK_UUID}:802-11-wireless:{INTERFACE}\n"


class Runner:
    def __init__(self, active_wifi=ACTIVE_WIFI):
        self.active_wifi = active_wifi
        self.calls = []

    def __call__(self, argv, *, timeout, capture=False):
        self.calls.append(list(argv))
        stdout = ""
        if argv[:4] == ["nmcli", "--terse", "--fields", "UUID,TYPE,DEVICE"]:
            stdout = self.active_wifi
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)


@pytest.fixture
def setup_module_api():
    from robopark_host import host_resilience

    return host_resilience


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ROBOPARK_TESTING", "1")
    for relative in (
        "etc/robopark",
        "etc/systemd/system",
        "etc/systemd/sleep.conf.d",
        "etc/systemd/journald.conf.d",
        "usr/local/libexec",
    ):
        (tmp_path / relative).mkdir(parents=True)
    return tmp_path


def network_config(root: Path) -> Path:
    return root / "etc/robopark/network-recovery.json"


def write_network_config(root: Path, value: dict) -> bytes:
    content = (json.dumps(value, sort_keys=True) + "\n").encode()
    target = network_config(root)
    target.write_bytes(content)
    target.chmod(0o600)
    return content


def test_invalid_existing_config_fails_before_commands_or_writes(
    setup_module_api, fake_root
):
    target = network_config(fake_root)
    target.write_text('{"enabled":true,"interface":"bad;name"}\n')
    target.chmod(0o600)
    runner = Runner()

    with pytest.raises(ValueError, match="invalid_network_recovery_config"):
        setup_module_api.setup(root=fake_root, release_root=ROOT, runner=runner)

    assert runner.calls == []
    assert not (fake_root / "usr/local/libexec/robopark-network-recovery.py").exists()
    assert not (fake_root / "etc/systemd/system/sleep.target").exists()


def test_missing_wifi_leaves_config_absent_so_later_setup_can_detect_it(
    setup_module_api, fake_root
):
    no_wifi = Runner(active_wifi="")
    setup_module_api.setup(root=fake_root, release_root=ROOT, runner=no_wifi)
    assert not network_config(fake_root).exists()

    wifi = Runner()
    setup_module_api.setup(root=fake_root, release_root=ROOT, runner=wifi)
    assert json.loads(network_config(fake_root).read_text()) == {
        "connection_uuid": NETWORK_UUID,
        "enabled": True,
        "interface": INTERFACE,
    }
    assert stat.S_IMODE(network_config(fake_root).stat().st_mode) == 0o600


def test_existing_disabled_config_is_preserved_without_network_mutation(
    setup_module_api, fake_root
):
    original = write_network_config(fake_root, {"enabled": False})
    runner = Runner()

    setup_module_api.setup(root=fake_root, release_root=ROOT, runner=runner)

    assert network_config(fake_root).read_bytes() == original
    assert not any(call[:3] == ["nmcli", "connection", "modify"] for call in runner.calls)
    assert not any(call[:2] == ["iw", "dev"] for call in runner.calls)


def test_enabled_profile_is_tuned_live_without_disconnect_or_reapply(
    setup_module_api, fake_root
):
    original = write_network_config(
        fake_root,
        {
            "enabled": True,
            "interface": INTERFACE,
            "connection_uuid": NETWORK_UUID,
        },
    )
    runner = Runner()

    setup_module_api.setup(root=fake_root, release_root=ROOT, runner=runner)

    assert network_config(fake_root).read_bytes() == original
    assert [
        "nmcli",
        "connection",
        "modify",
        "uuid",
        NETWORK_UUID,
        "connection.autoconnect",
        "yes",
        "connection.autoconnect-retries",
        "0",
        "802-11-wireless.powersave",
        "2",
        "ipv4.may-fail",
        "no",
        "ipv4.dhcp-timeout",
        "45",
    ] in runner.calls
    assert ["iw", "dev", INTERFACE, "set", "power_save", "off"] in runner.calls
    flattened = " ".join(" ".join(call) for call in runner.calls)
    assert "disconnect" not in flattened
    assert "reapply" not in flattened
    assert "NetworkManager" not in flattened
    assert "docker" not in flattened


def test_setup_installs_standalone_and_persistent_host_policy(
    setup_module_api, fake_root
):
    runner = Runner(active_wifi="")

    result = setup_module_api.setup(root=fake_root, release_root=ROOT, runner=runner)

    installed = fake_root / "usr/local/libexec/robopark-network-recovery.py"
    source = ROOT / "deploy/host/robopark_host/network_recovery.py"
    assert installed.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755
    sleep = fake_root / "etc/systemd/sleep.conf.d/90-robopark-no-sleep.conf"
    assert sleep.read_text() == (
        "[Sleep]\n"
        "AllowSuspend=no\n"
        "AllowHibernation=no\n"
        "AllowSuspendThenHibernate=no\n"
        "AllowHybridSleep=no\n"
    )
    for name in (
        "sleep.target",
        "suspend.target",
        "hibernate.target",
        "hybrid-sleep.target",
        "suspend-then-hibernate.target",
    ):
        assert os.readlink(fake_root / "etc/systemd/system" / name) == "/dev/null"
    dropin = (
        "[Unit]\n"
        "DefaultDependencies=no\n"
        "Requires=sysinit.target\n"
        "After=sysinit.target\n"
        "Conflicts=shutdown.target\n"
        "Before=shutdown.target\n"
    )
    for unit in (
        "robopark-commands.path",
        "robopark-bot.path",
        "robopark-storage-check.service",
    ):
        target = fake_root / "etc/systemd/system" / f"{unit}.d/10-robopark-boot-order.conf"
        assert target.read_text() == dropin
    assert ["systemctl", "daemon-reload"] in runner.calls
    assert [
        "systemctl",
        "enable",
        "--now",
        "robopark-network-recovery.timer",
    ] in runner.calls
    assert result["state"] == "ready"


def test_journal_restart_and_flush_happen_only_when_policy_changes(
    setup_module_api, fake_root
):
    runner = Runner(active_wifi="")
    setup_module_api.setup(root=fake_root, release_root=ROOT, runner=runner)
    assert [
        "systemd-tmpfiles",
        "--create",
        "--prefix=/var/log/journal",
    ] in runner.calls
    assert ["systemctl", "restart", "systemd-journald.service"] in runner.calls
    assert ["journalctl", "--flush"] in runner.calls

    runner.calls.clear()
    setup_module_api.setup(root=fake_root, release_root=ROOT, runner=runner)
    assert not any("systemd-journald.service" in call for call in runner.calls)
    assert not any(call[:1] == ["journalctl"] for call in runner.calls)
    assert not any(call[:1] == ["systemd-tmpfiles"] for call in runner.calls)


def test_symlinked_managed_parent_fails_before_commands_or_external_write(
    setup_module_api, fake_root, tmp_path
):
    victim = tmp_path / "victim"
    victim.mkdir()
    parent = fake_root / "usr/local/libexec"
    parent.rmdir()
    parent.symlink_to(victim, target_is_directory=True)
    runner = Runner()

    with pytest.raises(ValueError, match="unsafe_resilience_path"):
        setup_module_api.setup(root=fake_root, release_root=ROOT, runner=runner)

    assert runner.calls == []
    assert list(victim.iterdir()) == []


def test_symlinked_managed_file_is_rejected_without_touching_target(
    setup_module_api, fake_root, tmp_path
):
    victim = tmp_path / "victim.py"
    victim.write_text("keep\n")
    target = fake_root / "usr/local/libexec/robopark-network-recovery.py"
    target.symlink_to(victim)
    runner = Runner()

    with pytest.raises(ValueError, match="unsafe_resilience_path"):
        setup_module_api.setup(root=fake_root, release_root=ROOT, runner=runner)

    assert runner.calls == []
    assert victim.read_text() == "keep\n"


def test_setup_service_runs_once_outside_the_updater_sandbox():
    parsed = configparser.ConfigParser(interpolation=None)
    parsed.optionxform = str
    parsed.read(ROOT / "deploy/systemd/robopark-resilience-setup.service")

    service = parsed["Service"]
    assert service["Type"] == "oneshot"
    assert service["User"] == "root"
    assert service["ExecStart"] == (
        "/usr/bin/python3 -I /opt/robopark/host-tools/robopark-host-resilience"
    )
    assert "RemainAfterExit" not in service
    assert "Install" not in parsed
