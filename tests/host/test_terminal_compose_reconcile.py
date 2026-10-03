"""Repair the API bridge when an older updater renders a new terminal release."""

import copy
import json
import os

import pytest
from robopark_host import cli, restore, terminal_install
from robopark_host.release import ReleaseError
from robopark_host.state import atomic_write_json


@pytest.fixture
def legacy_compose(host_paths):
    release = host_paths.releases / "feature"
    units = release / "deploy/systemd"
    units.mkdir(parents=True)
    for name in terminal_install.TERMINAL_UNITS:
        (units / name).write_text("[Service]\n")
    host_paths.current.symlink_to(release)
    document = {
        "x-robopark-release": str(release),
        "services": {
            "api": {
                "image": "sha256:api",
                "environment": {"KEEP": "value"},
                "volumes": [],
            },
            "worker": {"image": "sha256:worker"},
            "db": {
                "image": "sha256:db",
                "volumes": [{"type": "volume", "source": "data", "target": "/data"}],
            },
        },
        "volumes": {"data": {}},
    }
    target = host_paths.state / "compose/operation-production.json"
    atomic_write_json(target, document)
    (host_paths.state / "current-compose.json").symlink_to(target)
    return target, document


def repair(paths):
    helper = getattr(terminal_install, "reconcile_terminal_compose", None)
    assert callable(helper), "boot must repair an older updater's missing API bridge"
    return helper(paths)


def test_repairs_only_api_and_preserves_pinned_services(host_paths, legacy_compose):
    target, before = legacy_compose
    assert repair(host_paths) is True
    expected = copy.deepcopy(before)
    expected["services"]["api"]["environment"]["TERMINAL_BROKER_SOCKET"] = (
        "/run/robopark-terminal/broker.sock"
    )
    expected["services"]["api"]["volumes"].append(
        {
            "type": "bind",
            "source": str(host_paths.root / "run/robopark-terminal"),
            "target": "/run/robopark-terminal",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    )
    assert json.loads(target.read_text()) == expected
    assert target.stat().st_mode & 0o777 == 0o600
    identity = target.stat()
    assert repair(host_paths) is False
    assert target.stat().st_ino == identity.st_ino
    assert target.stat().st_mtime_ns == identity.st_mtime_ns


def test_foundation_without_terminal_needs_no_compose(host_paths):
    release = host_paths.releases / "foundation"
    release.mkdir(parents=True)
    host_paths.current.symlink_to(release)
    assert repair(host_paths) is False


@pytest.mark.parametrize(
    "damage",
    [
        "mode",
        "hardlink",
        "oversize",
        "duplicate_json",
        "release_mismatch",
        "outside",
        "indirect_symlink",
        "parent_symlink",
        "not_symlink",
        "api_shape",
        "environment_shape",
        "volumes_shape",
        "socket_conflict",
        "mount_conflict",
        "mount_duplicate",
        "worker_environment",
        "worker_mount",
    ],
)
def test_rejects_unsafe_or_conflicting_compose_without_mutation(
    host_paths, legacy_compose, damage
):
    target, document = legacy_compose
    link = host_paths.state / "current-compose.json"
    mount = {
        "type": "bind",
        "source": str(host_paths.root / "run/robopark-terminal"),
        "target": "/run/robopark-terminal",
        "read_only": True,
        "bind": {"create_host_path": False},
    }
    if damage == "mode":
        target.chmod(0o644)
    elif damage == "hardlink":
        os.link(target, target.with_suffix(".other"))
    elif damage == "oversize":
        target.write_text(" " * (1024 * 1024 + 1))
    elif damage == "duplicate_json":
        target.write_text('{"services":{},"services":{}}')
    elif damage in {"outside", "indirect_symlink", "not_symlink", "parent_symlink"}:
        link.unlink()
        if damage == "outside":
            target = host_paths.root / "outside.json"
            atomic_write_json(target, document)
            link.symlink_to(target)
        elif damage == "indirect_symlink":
            alias = target.with_suffix(".alias")
            alias.symlink_to(target)
            link.symlink_to(alias)
        elif damage == "parent_symlink":
            alias = host_paths.state / "alias"
            alias.symlink_to(target.parent, target_is_directory=True)
            link.symlink_to(alias / target.name)
        else:
            atomic_write_json(link, document)
    else:
        api = document["services"]["api"]
        if damage == "release_mismatch":
            document["x-robopark-release"] = "/unrelated"
        elif damage == "api_shape":
            document["services"]["api"] = []
        elif damage == "environment_shape":
            api["environment"] = []
        elif damage == "volumes_shape":
            api["volumes"] = {}
        elif damage == "socket_conflict":
            api["environment"]["TERMINAL_BROKER_SOCKET"] = "/other.sock"
        elif damage == "mount_conflict":
            api["volumes"] = [{**mount, "read_only": False}]
        elif damage == "mount_duplicate":
            api["volumes"] = [mount, mount]
        elif damage == "worker_environment":
            document["services"]["worker"]["environment"] = {
                "TERMINAL_BROKER_SOCKET": "/other.sock"
            }
        elif damage == "worker_mount":
            document["services"]["worker"]["volumes"] = [{**mount, "target": "/alias"}]
        atomic_write_json(target, document)
    before = target.read_bytes()
    with pytest.raises(ReleaseError, match="terminal_invalid_compose"):
        repair(host_paths)
    assert target.read_bytes() == before


def test_boot_repairs_after_recovery_and_caches_new_helper(
    host_paths, legacy_compose, monkeypatch
):
    target, _ = legacy_compose

    def recover(*args, **kwargs):
        # Recovery can switch the host-tools package back to an older release.
        monkeypatch.setattr(
            terminal_install, "reconcile_terminal_compose", None, raising=False
        )
        return 0

    monkeypatch.setattr(restore, "recover_restore", recover)
    assert cli.main(["restore", "--boot-recover"]) == 0
    assert (
        json.loads(target.read_text())["services"]["api"]["environment"].get(
            "TERMINAL_BROKER_SOCKET"
        )
        == "/run/robopark-terminal/broker.sock"
    )


@pytest.mark.parametrize(
    "code,active,automatic", [(75, False, True), (0, True, True), (0, False, False)]
)
def test_does_not_repair_during_handoff_failure_or_manual_restore(
    host_paths, legacy_compose, monkeypatch, code, active, automatic
):
    target, _ = legacy_compose
    before = target.read_bytes()
    monkeypatch.setattr(restore, "recover_restore", lambda *args, **kwargs: code)
    monkeypatch.setattr(restore, "active_restore", lambda paths: active)
    assert cli.main(["restore", "--boot-recover" if automatic else "--recover"]) == code
    assert target.read_bytes() == before


def test_invalid_bridge_blocks_boot(host_paths, legacy_compose, monkeypatch):
    target, _ = legacy_compose
    target.chmod(0o644)
    monkeypatch.setattr(restore, "recover_restore", lambda *args, **kwargs: 0)
    assert cli.main(["restore", "--boot-recover"]) != 0
