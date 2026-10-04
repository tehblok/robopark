"""The first OTA hands storage policy from the published agent to its candidate."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "deploy/host/robopark-storage-reconcile"
# Frozen from published rc26 commit ed49a361 (`git show ...:storage_setup.py`).
RC26_STORAGE_SETUP_SHA256 = (
    "19431f6fb4bca0f79728bc0fc4f5da9187a4936ef3b321e3f6d0438e8e96e81e"
)
RC26_WRITERS = {
    "robopark.service",
    "robopark-ai-broker.service",
    "robopark-ai-setup.service",
    "robopark-ai.service",
    "robopark-backup.service",
    "robopark-bot.service",
    "robopark-commands.service",
    "robopark-doctor.service",
    "robopark-terminal-broker.service",
    "robopark-terminal-maintenance@.service",
    "robopark-terminal-root@.service",
    "robopark-terminal-setup.service",
    "robopark-tuna.service",
    "robopark-updater.service",
    "robopark-watchdog.service",
}


def test_core_checks_reconciled_storage_after_boot_recovery_selects_release():
    service = (ROOT / "deploy/systemd/robopark.service").read_text()
    check = (
        "ExecStartPre=/usr/bin/python3 -I "
        "/opt/robopark/host-tools/robopark-storage-reconcile --check"
    )
    recover = (
        "ExecStartPre=/usr/bin/python3 -I "
        "/opt/robopark/host-tools/robopark restore --boot-recover"
    )
    setup = (ROOT / "deploy/systemd/robopark-terminal-setup.service").read_text()
    apply = (
        "ExecStartPre=/usr/bin/python3 -I "
        "/opt/robopark/host-tools/robopark-storage-reconcile --apply"
    )

    assert HELPER.is_file()
    assert service.index(recover) < service.index(check) < service.index("ExecStart=")
    assert setup.index(apply) < setup.index("ExecStart=/usr/bin/python3")
    assert "After=docker.service" in setup
    assert "robopark-terminal-setup.service" in service.split("After=", 1)[1].splitlines()[0]


def test_candidate_started_ai_units_fail_closed_without_storage_receipt():
    check = (
        "ExecStartPre=+/usr/bin/python3 -I "
        "/opt/robopark/host-tools/robopark-storage-reconcile --check"
    )
    for name in (
        "robopark-ai-broker.service",
        "robopark-ai-setup.service",
        "robopark-ai.service",
    ):
        service = (ROOT / "deploy/systemd" / name).read_text()
        assert service.index(check) < service.index("ExecStart=")


def test_writer_added_after_rc26_must_self_guard_its_first_start():
    """A future unit can be scheduled beside core before global reconciliation."""

    for path in (ROOT / "deploy/systemd").glob("robopark*.service"):
        if path.name in RC26_WRITERS:
            continue
        commands = [
            line
            for line in path.read_text().splitlines()
            if line.startswith(("ExecStart=", "ExecStartPre="))
        ]
        assert commands and "robopark-storage-reconcile" in commands[0], path.name


def test_both_rollbacks_force_previous_storage_apply_before_core_check(
    host_paths,
):
    from robopark_host.ota_update import SystemOtaUpdateRuntime
    from robopark_host.rollback import rollback_release
    from robopark_host.terminal_install import (
        BROKER_UNIT,
        TERMINAL_UNITS,
        quiesce_terminal,
    )

    installed = host_paths.root / "etc/systemd/system" / BROKER_UNIT
    installed.parent.mkdir(parents=True)
    installed.write_text("installed\n")
    calls = []

    class Runner:
        def run(self, command, **_kwargs):
            calls.append(command)
            if "--property=ActiveState" in command:
                return "inactive\n"
            if "list-units" in command:
                return ""
            return ""

    quiesce_terminal(host_paths, Runner(), reason="rollback")
    assert ["systemctl", "stop", TERMINAL_UNITS[0]] in calls

    # Both rollback engines stop the RemainAfterExit setup service before they
    # restore the previous unit and restart core. Core Wants+After then starts
    # previous --apply and refreshes its receipt before previous --check.
    for rollback in (rollback_release, SystemOtaUpdateRuntime.rollback):
        source = inspect.getsource(rollback)
        assert source.index("quiesce_terminal") < source.index(
            '["systemctl", "restart", "robopark.service"]'
        )
    core = (ROOT / "deploy/systemd/robopark.service").read_text()
    assert "Wants=network-online.target robopark-bot.path robopark-terminal-setup.service" in core


def test_published_agent_hands_reconciliation_to_candidate(
    host_paths, monkeypatch
):
    """Run rc26's actual refresh, then the candidate renderer through its hook."""

    from robopark_host import storage_setup as candidate_setup

    candidate = host_paths.releases / "candidate"
    package = candidate / "deploy/host/robopark_host"
    package.mkdir(parents=True)
    for name in (
        "storage_layout.py",
        "storage_compatibility.py",
        "storage_setup.py",
        "storage_watchdog.py",
    ):
        source = ROOT / "deploy/host/robopark_host" / name
        (package / name).write_bytes(source.read_bytes())
    (candidate / "deploy/storage-layout-version").write_text("1\n")
    candidate_helper = candidate / "deploy/host" / HELPER.name
    candidate_helper.write_bytes(HELPER.read_bytes())
    candidate_helper.chmod(0o755)
    host_paths.current.symlink_to(candidate)
    layout = {
        "schema": 1,
        "state": "ready",
        "mode": "emmc-nvme-data",
        "device": "/dev/nvme0n1p1",
        "uuid": "11111111-2222-3333-4444-555555555555",
        "root_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "min_free_bytes": 64 * 1024**2,
    }

    # Load the exact published rc26 implementation in an isolated package.
    legacy_package = ModuleType("published_rc26")
    legacy_package.__path__ = []
    legacy_layout = ModuleType("published_rc26.storage_layout")
    legacy_layout.LAYOUT_PATH = "/etc/robopark-storage/layout.json"
    legacy_layout.STORAGE_ROOT = "/srv/robopark"
    legacy_layout.TARGETS = {}
    legacy_layout.StorageError = RuntimeError
    legacy_layout.inspect_storage = lambda _root: {}
    legacy_layout.load_layout = lambda _root: layout
    legacy_layout.require_storage = lambda *_args, **_kwargs: layout
    monkeypatch.setitem(sys.modules, "published_rc26", legacy_package)
    monkeypatch.setitem(sys.modules, "published_rc26.storage_layout", legacy_layout)
    legacy_source = (
        ROOT / "tests/host/fixtures/published_rc26/storage_setup.py"
    )
    assert hashlib.sha256(legacy_source.read_bytes()).hexdigest() == (
        RC26_STORAGE_SETUP_SHA256
    )
    spec = importlib.util.spec_from_file_location(
        "published_rc26.storage_setup", legacy_source
    )
    assert spec is not None and spec.loader is not None
    legacy_setup = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, legacy_setup)
    spec.loader.exec_module(legacy_setup)

    future_name = "robopark-future-writer.service"
    future = (
        host_paths.root
        / f"etc/systemd/system/{future_name}.d/50-robopark-storage.conf"
    )
    legacy_setup.refresh_storage_guard(host_paths.root, candidate)
    assert not future.exists()

    # The candidate adds a writer unknown to rc26. Its actual renderer must be
    # used by the handoff rather than the already-imported legacy module.
    monkeypatch.setattr(candidate_setup, "load_layout", lambda _root: layout)
    monkeypatch.setattr(
        candidate_setup, "require_storage", lambda *_args, **_kwargs: layout
    )
    monkeypatch.setattr(
        candidate_setup, "require_managed_container_roots", lambda _root: None
    )
    monkeypatch.setattr(
        "robopark_host.storage_layout.require_storage",
        lambda *_args, **_kwargs: layout,
    )
    monkeypatch.setattr(
        candidate_setup, "_WRITERS", (*candidate_setup._WRITERS, future_name)
    )
    loader = importlib.machinery.SourceFileLoader(
        "candidate_storage_reconcile", str(HELPER)
    )
    helper_spec = importlib.util.spec_from_loader(loader.name, loader)
    assert helper_spec is not None
    helper = importlib.util.module_from_spec(helper_spec)
    loader.exec_module(helper)
    monkeypatch.setattr(helper, "RELEASE", candidate)

    helper._apply(host_paths, candidate)

    assert future.read_text() == candidate_setup.storage_units(layout)[
        f"{future_name}.d/50-robopark-storage.conf"
    ]
    helper._check_receipt(host_paths, candidate)
    (host_paths.state / "storage-policy-release.json").write_text(
        '{"schema":1,"release":"stale"}'
    )
    with pytest.raises(RuntimeError, match="storage_policy_not_reconciled"):
        helper._check_receipt(host_paths, candidate)
