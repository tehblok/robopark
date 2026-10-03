"""Ingress recovery when a previous updater restarts the new application unit."""
from pathlib import Path
import json
import os
import shlex
import shutil
import subprocess
import sys

import pytest

from robopark_host import cli, updater
from robopark_ota.local_update import restart_enabled_tuna


class ServiceManager:
    def __init__(self, enabled="enabled", active=False, core_starting=False):
        self.enabled = enabled
        self.active = active
        self.core_starting = core_starting
        self.queued = False
        self.commands = []

    def run(self, argv, **kwargs):
        self.commands.append(argv)
        if argv[1] == "show":
            return (self.enabled + "\n").encode()
        if argv[1] == "reset-failed":
            return b""
        if argv[1] == "is-active":
            return b"active\n" if self.active else b"inactive\n"
        if argv[1] == "restart":
            raise RuntimeError("would_disconnect_active_tunnel")
        if argv[1] == "start":
            if self.core_starting and "--no-block" not in argv:
                raise RuntimeError("tuna_after_core_start_deadlock")
            self.queued = True
            if not self.core_starting:
                self.active = True
            return b""
        raise AssertionError(argv)


def test_new_application_unit_recovers_ingress_even_with_previous_web_updater():
    unit = (Path(__file__).resolve().parents[2] / "deploy/systemd/robopark.service").read_text()
    assert "ExecStartPost=-/usr/bin/python3 -I /opt/robopark/host-tools/robopark tuna-start\n" in unit


@pytest.mark.parametrize("enabled", ["enabled", "enabled-runtime"])
def test_core_post_start_queues_tuna_without_dependency_deadlock(monkeypatch, host_paths, enabled):
    manager = ServiceManager(enabled=enabled, core_starting=True)
    monkeypatch.setattr(updater, "SystemRunner", lambda: manager)
    monkeypatch.setattr(cli, "paths_from_environment", lambda: host_paths)

    assert cli.main(["tuna-start"]) == 0
    assert manager.queued
    assert ["systemctl", "start", "--no-block", "robopark-tuna.service"] in manager.commands
    assert not any(argv[1] == "is-active" for argv in manager.commands)


@pytest.mark.parametrize("enabled", ["disabled", "masked", "static", "not-found"])
def test_core_post_start_preserves_disabled_or_unmanaged_ingress(monkeypatch, host_paths, enabled):
    manager = ServiceManager(enabled=enabled, core_starting=True)
    monkeypatch.setattr(updater, "SystemRunner", lambda: manager)
    monkeypatch.setattr(cli, "paths_from_environment", lambda: host_paths)

    assert cli.main(["tuna-start"]) == 0
    assert not manager.queued
    assert len(manager.commands) == 1


def test_update_finalization_keeps_an_already_running_tunnel_connected():
    manager = ServiceManager(active=True)
    assert restart_enabled_tuna(manager) == "active"
    assert manager.active
    assert not any(argv[1] == "restart" for argv in manager.commands)


def test_core_post_start_reports_failure_to_queue_ingress(monkeypatch, host_paths, capsys):
    class UnavailableManager(ServiceManager):
        def run(self, argv, **kwargs):
            if argv[1] == "start":
                raise RuntimeError("service_manager_unavailable")
            return super().run(argv, **kwargs)

    monkeypatch.setattr(updater, "SystemRunner", UnavailableManager)
    monkeypatch.setattr(cli, "paths_from_environment", lambda: host_paths)
    assert cli.main(["tuna-start"]) == 1
    assert json.loads(capsys.readouterr().out) == {"tuna": "failed"}


def test_activated_release_recovers_ingress_in_isolated_post_start_process(host_paths):
    """Use published unit, trusted launcher and real SystemRunner, no OTA resume."""
    root = Path(__file__).resolve().parents[2]
    candidate = host_paths.releases / "new-release"
    (candidate / "deploy").mkdir(parents=True)
    for folder in ("host", "ota", "systemd"):
        shutil.copytree(root / "deploy" / folder, candidate / "deploy" / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    host_paths.etc.mkdir(parents=True)
    (host_paths.root / "etc/systemd/system").mkdir(parents=True)
    (host_paths.root / "etc/tmpfiles.d").mkdir(parents=True)
    (host_paths.opt / "host-tools").symlink_to(candidate / "deploy/host")
    updater._activate_system_files(host_paths, candidate)
    unit = (host_paths.root / "etc/systemd/system/robopark.service").read_text()
    directive = next(line.split("=", 1)[1] for line in unit.splitlines()
                     if line.startswith("ExecStartPost=") and line.endswith(" tuna-start"))
    command = shlex.split(directive.removeprefix("-"))
    command[0] = sys.executable
    command[2] = str(host_paths.opt / "host-tools/robopark")
    fake_bin = host_paths.root / "fake-bin"
    fake_bin.mkdir()
    control = fake_bin / "systemctl"
    control.write_text(f"#!{sys.executable}\n" + '''
import json, os, sys
from pathlib import Path
root = Path(os.environ["ROBOPARK_ROOT"])
args = sys.argv[1:]
with (root / "systemctl-commands.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if args[0] == "show":
    print("enabled")
elif args[0] == "reset-failed":
    pass
elif args == ["start", "--no-block", "robopark-tuna.service"]:
    (root / "tuna-start-queued").write_text("queued after core start")
else:
    sys.exit(1)  # A blocking start/restart would wait for the calling core unit.
''')
    control.chmod(0o700)
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10,
        env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
             "ROBOPARK_TESTING": "1", "ROBOPARK_ROOT": str(host_paths.root)})
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"tuna": "queued"}
    assert (host_paths.root / "tuna-start-queued").is_file()
