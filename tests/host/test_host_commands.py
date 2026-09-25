"""Root inbox consumer must claim once and export bounded public artifacts."""

import configparser
import json
import os
import struct
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport
from robopark_host.commands import OperationKind

TEST_BOOT_ID = "00000000-0000-4000-8000-000000000010"


@pytest.fixture(autouse=True)
def typed_test_boot_id(host_paths):
    """Give this command-consumer module the kernel boot identity production always has."""
    boot = host_paths.root / "proc/sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True, exist_ok=True)
    boot.write_text(TEST_BOOT_ID + "\n")


class FakeHostEffects:
    supported_kinds = frozenset(OperationKind)

    def __init__(self, *, failure=None, reconciliation=None):
        self.calls = []
        self.failure = failure
        self.reconciliation = reconciliation

    def _effect(self, name, operation_id, *values):
        from robopark_host.release import ReleaseError

        self.calls.append((name, operation_id, *values))
        if self.failure == name:
            raise ReleaseError("simulated_host_failure")
        return {"effect": name, "operation_id": operation_id}

    def reconcile(self, operation):
        self.calls.append(("reconcile", operation.operation_id, operation.kind.value))
        return self.reconciliation

    def release_update(self, operation_id, release_id):
        return self._effect("release_update", operation_id, release_id)

    def ota_update(self, operation_id, upload_id, sha256, version):
        return self._effect("ota_update", operation_id, upload_id, sha256, version)

    def reinstall(self, operation_id):
        return self._effect("reinstall", operation_id)

    def rollback(self, operation_id, release):
        return self._effect("rollback", operation_id, release)

    def usb_format(self, operation_id, device):
        self.calls.append(("usb_format", operation_id, device.uuid, device.path))
        if self.failure == "usb_format":
            from robopark_host.release import ReleaseError

            raise ReleaseError("simulated_host_failure")
        return {"device_uuid": device.uuid, "formatted": True}

    def reboot(self, operation_id):
        return self._effect("reboot", operation_id)

    def package_inspect(self, operation_id, package):
        return self._effect("package_inspect", operation_id, package)

    def package_update(self, operation_id, package):
        return self._effect("package_update", operation_id, package)

    def service_restart(self, operation_id, service):
        return self._effect("service_restart", operation_id, service)

    def backup(self, operation_id, device_uuid):
        return self._effect("backup", operation_id, device_uuid)

    def backup_verify(self, operation_id, backup_id):
        return self._effect("backup_verify", operation_id, backup_id)

    def backup_restore(self, operation_id, backup_id):
        return self._effect("backup_restore", operation_id, backup_id)

    def cleanup_preview(self, operation_id, categories):
        return self._effect("cleanup_preview", operation_id, *categories)

    def cleanup_execute(self, operation_id, plan_id):
        return self._effect("cleanup_execute", operation_id, plan_id)

    def diagnostics(self, operation_id):
        return self._effect("diagnostics", operation_id)

    def usb_discover(self, operation_id):
        return self._effect("usb_discover", operation_id)

    def usb_select(self, operation_id, device):
        return self._effect("usb_select", operation_id, device.uuid, device.path)


def request(paths, kind="diagnostics", **changes):
    command = {
        "job_id": str(uuid4()),
        "kind": kind,
        "actor_user_id": 7,
        "created_at": datetime.now(UTC).isoformat(),
    }
    command.update(changes)
    inbox = paths.ops / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "approved.json").write_text(json.dumps(command))
    return command


def typed_request(kind, **changes):
    from robopark_host.operation_capabilities import capability_revision, operation_capabilities

    operation_id = str(uuid4())
    value = {
        "job_id": operation_id,
        "kind": kind,
        "actor_user_id": 7,
        "created_at": datetime.now(UTC).isoformat(),
        "capability_revision": capability_revision(
            TEST_BOOT_ID, operation_capabilities(FakeHostEffects())
        ),
        "confirmation": f"ЗАПУСТИТЬ {kind.upper()}",
    }
    value.update(changes)
    value.setdefault("authorization", authorization(value))
    return value


def authorization(command):
    return {
        "operation_id": command["job_id"],
        "operation_kind": command["kind"],
        "actor_user_id": command["actor_user_id"],
        "consumed": True,
        "validated_at": command["created_at"],
    }


def revision_for(effects):
    from robopark_host.operation_capabilities import capability_revision, operation_capabilities

    return capability_revision(TEST_BOOT_ID, operation_capabilities(effects))


_DEVICE_UUID = "00000000-0000-4000-8000-000000000001"
_BACKUP_UUID = "00000000-0000-4000-8000-000000000002"
_PLAN_UUID = "00000000-0000-4000-8000-000000000003"


def publish_test_capabilities(paths, effects, monkeypatch):
    from robopark_api.services.ops import host_bridge
    from robopark_host.operation_capabilities import publish_operation_capabilities

    boot_id = "00000000-0000-4000-8000-000000000010"
    boot = paths.root / "proc/sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True, exist_ok=True)
    boot.write_text(boot_id + "\n")
    monkeypatch.setattr(host_bridge, "_host_boot_id", lambda: boot_id)
    publish_operation_capabilities(paths, effects)


@pytest.mark.parametrize(
    ("payload", "effect"),
    [
        ({"kind": "ota-update", "upload_id": _DEVICE_UUID, "sha256": "a" * 64, "version": "0.2.0-rc.7", "confirmation": "UPDATE ROBOPARK"}, "ota_update"),
        ({"kind": "release-update", "release_id": 7, "confirmation": "UPDATE ROBOPARK"}, "release_update"),
        ({"kind": "reinstall", "confirmation": "REINSTALL ROBOPARK"}, "reinstall"),
        ({"kind": "rollback", "release": "release-a", "confirmation": "ROLLBACK ROBOPARK"}, "rollback"),
        ({"kind": "package-inspect", "package": "openssl", "confirmation": "ЗАПУСТИТЬ PACKAGE-INSPECT"}, "package_inspect"),
        ({"kind": "package-update", "package": "openssl", "confirmation": "UPDATE PACKAGE openssl"}, "package_update"),
        ({"kind": "service-restart", "service": "robopark-api.service", "confirmation": "RESTART SERVICE robopark-api.service"}, "service_restart"),
        ({"kind": "reboot", "confirmation": "REBOOT ROBOPARK"}, "reboot"),
        ({"kind": "backup", "device_uuid": _DEVICE_UUID, "confirmation": "BACKUP ROBOPARK"}, "backup"),
        ({"kind": "backup-verify", "backup_id": _BACKUP_UUID, "confirmation": "ЗАПУСТИТЬ BACKUP-VERIFY"}, "backup_verify"),
        ({"kind": "backup-restore", "backup_id": _BACKUP_UUID, "confirmation": "RESTORE ROBOPARK BACKUP"}, "backup_restore"),
        ({"kind": "cleanup-preview", "categories": ["backups", "releases"], "confirmation": "ЗАПУСТИТЬ CLEANUP-PREVIEW"}, "cleanup_preview"),
        ({"kind": "cleanup-execute", "plan_id": _PLAN_UUID, "confirmation": "CLEAN ROBOPARK"}, "cleanup_execute"),
        ({"kind": "diagnostics", "confirmation": "ЗАПУСТИТЬ DIAGNOSTICS"}, "diagnostics"),
        ({"kind": "usb-discover", "confirmation": "ЗАПУСТИТЬ USB-DISCOVER"}, "usb_discover"),
        ({"kind": "usb-format", "device_uuid": _DEVICE_UUID, "confirmation": f"FORMAT USB {_DEVICE_UUID}", "confirmation_repeat": f"FORMAT USB {_DEVICE_UUID}"}, "usb_format"),
        ({"kind": "usb-select", "device_uuid": _DEVICE_UUID, "confirmation": "ЗАПУСТИТЬ USB-SELECT"}, "usb_select"),
    ],
)
def test_api_bridge_to_real_consumer_dispatches_every_typed_kind(
    host_paths, payload, effect, monkeypatch
):
    """The production consumer path must accept the API bridge's exact envelope."""

    from robopark_api.services.ops import host_bridge
    from robopark_host.commands import (
        BlockDevice,
        SystemTypedHostEffects,
        consume_commands,
    )

    operation_id = str(uuid4())
    api_ops = host_paths.var / "api-ops"
    (host_paths.ops / "inbox").mkdir(parents=True, exist_ok=True)
    (host_paths.ops / "public").mkdir(parents=True, exist_ok=True)
    (host_paths.ops / "public/command-claim.json").write_text(
        json.dumps({"job_id": str(uuid4()), "kind": "diagnostics", "actor_user_id": 1, "active": False})
    )
    system = FakeHostEffects()
    effects = SystemTypedHostEffects(host_paths, system=system)
    publish_test_capabilities(host_paths, effects, monkeypatch)
    request_payload = {
        "operation_id": operation_id,
        "capability_revision": revision_for(effects),
        **payload,
    }
    host_bridge.enqueue_typed_operation(
        api_ops,
        host_paths.ops,
        request_payload,
        7,
        "session-hash",
        authorization_consumed={
            "operation_id": operation_id,
            "operation_kind": payload["kind"],
            "actor_user_id": 7,
            "consumed": True,
        },
    )
    devices = [BlockDevice(_DEVICE_UUID, "/dev/fake-usb", removable=True)]

    code = consume_commands(
        host_paths,
        lambda *args, **kwargs: None,
        object(),
        typed_effects=effects,
        typed_devices=lambda: devices,
    )

    assert code == 0
    assert system.calls[0][0] == effect
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["job_id"] == operation_id
    assert result["kind"] == payload["kind"]
    assert result["state"] == "succeeded"


@pytest.mark.parametrize(
    ("kind", "changes", "expected_state", "expected_error"),
    [
        ("package-inspect", {"package": "openssl"}, "succeeded", None),
        ("cleanup-preview", {"categories": ["diagnostics"]}, "succeeded", None),
        ("diagnostics", {}, "succeeded", None),
        ("usb-discover", {}, "succeeded", None),
        (
            "reboot",
            {"confirmation": "REBOOT ROBOPARK"},
            "failed",
            "capability_unavailable",
        ),
    ],
)
def test_default_cli_consumer_uses_safe_production_adapter(
    host_paths, kind, changes, expected_state, expected_error, monkeypatch
):
    from robopark_host import commands
    from robopark_host.cli import _consume_handler, _production_typed_effects

    monkeypatch.setattr(
        commands,
        "run_doctor",
        lambda *args: DiagnosticReport([CheckResult("resources", "ok", "ok")]),
    )

    status = host_paths.root / "var/lib/dpkg/status"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(
        "Package: openssl\nStatus: install ok installed\nVersion: 3.0.0\n\n"
    )
    device = host_paths.root / "dev/fake-usb"
    device.parent.mkdir(parents=True, exist_ok=True)
    device.touch()
    uuid_root = host_paths.root / "dev/disk/by-uuid"
    uuid_root.mkdir(parents=True)
    (uuid_root / _DEVICE_UUID).symlink_to("../../fake-usb")
    removable = host_paths.root / "sys/class/block/fake-usb/removable"
    removable.parent.mkdir(parents=True)
    removable.write_text("1\n")
    mountinfo = host_paths.root / "proc/self/mountinfo"
    mountinfo.parent.mkdir(parents=True)
    mountinfo.write_text("")
    command = typed_request(
        kind,
        capability_revision=revision_for(_production_typed_effects(host_paths)),
        **changes,
    )
    command["authorization"] = authorization(command)
    inbox = host_paths.ops / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "approved.json").write_text(json.dumps(command))

    code = _consume_handler(host_paths)

    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert code == int(expected_state != "succeeded")
    assert result["state"] == expected_state
    assert result["error"] == expected_error
    if kind == "package-inspect":
        assert result["detail"] == {
            "package": "openssl",
            "installed": True,
            "version": "3.0.0",
        }
    if kind == "usb-discover":
        assert result["detail"]["devices"] == [
            {"device_uuid": _DEVICE_UUID, "mounted": False, "removable": True}
        ]


def test_default_cli_consumer_verifies_backup_with_external_runtime_key(host_paths):
    from robopark_host.cli import _consume_handler, _production_typed_effects
    from robopark_host.commands import create_encrypted_backup

    key = b"v" * 32
    key_path = host_paths.root / "run/robopark/recovery.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    source = host_paths.root / "backup-source"
    source.mkdir()
    (source / "data").write_bytes(b"safe")
    mount = host_paths.root / "mnt/usb"
    backups = mount / "robopark-backups"
    backups.mkdir(parents=True)
    create_encrypted_backup(
        source,
        backups / f"backup-{_BACKUP_UUID}.rpb",
        recovery_key=key,
        app_version="0.2.0-rc.6",
        schema_version="0050_media_action_dependency",
    )
    device = host_paths.root / "dev/fake-usb"
    device.parent.mkdir(parents=True)
    device.touch()
    uuid_root = host_paths.root / "dev/disk/by-uuid"
    uuid_root.mkdir(parents=True)
    (uuid_root / _DEVICE_UUID).symlink_to("../../fake-usb")
    removable = host_paths.root / "sys/class/block/fake-usb/removable"
    removable.parent.mkdir(parents=True)
    removable.write_text("1\n")
    mountinfo = host_paths.root / "proc/self/mountinfo"
    mountinfo.parent.mkdir(parents=True)
    mountinfo.write_text(
        "1 0 8:1 / /mnt/usb rw - ext4 /dev/fake-usb rw\n"
    )
    from robopark_host.state import atomic_write_json

    atomic_write_json(
        host_paths.state / "selected-usb.json",
        {"schema": 1, "device_uuid": _DEVICE_UUID},
    )
    command = typed_request(
        "backup-verify",
        backup_id=_BACKUP_UUID,
        capability_revision=revision_for(_production_typed_effects(host_paths)),
    )
    command["authorization"] = authorization(command)
    inbox = host_paths.ops / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "approved.json").write_text(json.dumps(command))

    assert _consume_handler(host_paths) == 0
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["detail"] == {"backup_id": _BACKUP_UUID, "verified": True}
    receipt = json.loads(
        (host_paths.state / "backup-receipts" / f"{_BACKUP_UUID}.json").read_text()
    )
    assert receipt["verified"] is True


def _production_backup_verify_fixture(host_paths):
    from robopark_host.commands import (
        BlockDevice,
        SafeProductionTypedHostEffects,
        create_encrypted_backup,
    )

    key = b"c" * 32
    host_paths.state.mkdir(parents=True, exist_ok=True)
    key_path = host_paths.root / "run/robopark/recovery.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    source = host_paths.root / "confinement-source"
    source.mkdir()
    (source / "data").write_bytes(b"confined")
    mount = host_paths.root / "mnt/usb"
    mount.mkdir(parents=True)
    device = BlockDevice(
        _DEVICE_UUID,
        "/dev/fake-usb",
        removable=True,
        mounted=True,
        mount_point="/mnt/usb",
    )
    adapter = SafeProductionTypedHostEffects(
        host_paths, device_provider=lambda: (device,)
    )
    adapter.usb_select(str(uuid4()), device)

    def create(directory):
        directory.mkdir(parents=True, exist_ok=True)
        artifact = directory / f"backup-{_BACKUP_UUID}.rpb"
        create_encrypted_backup(
            source,
            artifact,
            recovery_key=key,
            app_version="0.2.0-rc.6",
            schema_version="0050_media_action_dependency",
        )
        artifact.chmod(0o600)
        return artifact

    return adapter, mount, create


@pytest.mark.parametrize("alias", ["backups", "mount", "ancestor", "artifact"])
def test_backup_verify_rejects_symlinked_usb_path_components(host_paths, alias):
    from robopark_host.commands import BlockDevice
    from robopark_host.release import ReleaseError

    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    outside = host_paths.root / "host-local-backups"
    artifact = create(outside)
    if alias == "backups":
        (mount / "robopark-backups").symlink_to(outside, target_is_directory=True)
    elif alias == "mount":
        original = host_paths.root / "mnt/real-usb"
        original.mkdir(parents=True)
        (original / "robopark-backups").mkdir()
        artifact.replace(original / "robopark-backups" / artifact.name)
        mount.rmdir()
        mount.symlink_to(original, target_is_directory=True)
    elif alias == "ancestor":
        original = host_paths.root / "mnt/real-parent/usb"
        (original / "robopark-backups").mkdir(parents=True)
        artifact.replace(original / "robopark-backups" / artifact.name)
        (host_paths.root / "mnt/alias").symlink_to(
            original.parent, target_is_directory=True
        )
        device = BlockDevice(
            _DEVICE_UUID,
            "/dev/fake-usb",
            removable=True,
            mounted=True,
            mount_point="/mnt/alias/usb",
        )
        adapter.device_provider = lambda: (device,)
    else:
        backups = mount / "robopark-backups"
        backups.mkdir()
        (backups / artifact.name).symlink_to(artifact)

    with pytest.raises(ReleaseError, match="unsafe_backup_path"):
        adapter.backup_verify(str(uuid4()), _BACKUP_UUID)


def test_backup_verify_directory_swap_race_fails_closed(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.release import ReleaseError

    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    backups = mount / "robopark-backups"
    create(backups)
    outside = host_paths.root / "race-outside"
    create(outside)
    moved = mount / "moved-backups"
    real_open = commands.os.open
    raced = False

    def swap_then_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal raced
        if path == f"backup-{_BACKUP_UUID}.rpb" and dir_fd is not None and not raced:
            backups.rename(moved)
            backups.symlink_to(outside, target_is_directory=True)
            raced = True
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(commands.os, "open", swap_then_open)
    with pytest.raises(ReleaseError, match="unsafe_backup_path"):
        adapter.backup_verify(str(uuid4()), _BACKUP_UUID)


def test_backup_verify_accepts_regular_single_link_direct_child(host_paths):
    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    create(mount / "robopark-backups")

    assert adapter.backup_verify(str(uuid4()), _BACKUP_UUID) == {
        "backup_id": _BACKUP_UUID,
        "verified": True,
    }
    receipt = json.loads(
        (host_paths.state / "backup-receipts" / f"{_BACKUP_UUID}.json").read_text()
    )
    assert receipt["device_uuid"] == _DEVICE_UUID


def test_backup_verify_never_falls_back_to_unselected_usb(host_paths):
    from dataclasses import replace

    from robopark_host.release import ReleaseError

    adapter, _, create = _production_backup_verify_fixture(host_paths)
    selected = adapter.device_provider()[0]
    other = replace(selected, uuid=str(uuid4()), path="/dev/fake-other", mount_point="/mnt/other")
    adapter.device_provider = lambda: (selected, other)
    create(host_paths.root / "mnt/other/robopark-backups")

    with pytest.raises(ReleaseError, match="backup_not_found"):
        adapter.backup_verify(str(uuid4()), _BACKUP_UUID)
    assert not (host_paths.state / "backup-receipts").exists()


def test_backup_verify_ignores_same_backup_id_on_other_usb(host_paths):
    from dataclasses import replace

    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    selected = adapter.device_provider()[0]
    other = replace(selected, uuid=str(uuid4()), path="/dev/fake-other", mount_point="/mnt/other")
    adapter.device_provider = lambda: (other, selected)
    create(host_paths.root / "mnt/other/robopark-backups")
    create(mount / "robopark-backups")
    assert adapter.backup_verify(str(uuid4()), _BACKUP_UUID)["verified"] is True
    receipt = adapter.verified_backup_receipt(_BACKUP_UUID)
    assert receipt["device_uuid"] == _DEVICE_UUID


@pytest.mark.parametrize("selection", ["missing", "invalid", "symlink", "duplicate"])
def test_backup_verify_requires_unambiguous_private_selection(host_paths, selection):
    from dataclasses import replace

    from robopark_host.release import ReleaseError

    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    create(mount / "robopark-backups")
    state = host_paths.state / "selected-usb.json"
    if selection == "missing":
        state.unlink()
    elif selection == "invalid":
        state.write_text(json.dumps({"schema": 1, "device_uuid": "not-a-uuid"}))
    elif selection == "symlink":
        outside = host_paths.root / "selection.json"
        state.replace(outside)
        state.symlink_to(outside)
    else:
        device = adapter.device_provider()[0]
        duplicate = replace(device, path="/dev/fake-other", mount_point="/mnt/other")
        adapter.device_provider = lambda: (device, duplicate)
    with pytest.raises(ReleaseError):
        adapter.backup_verify(str(uuid4()), _BACKUP_UUID)


@pytest.mark.parametrize("change", ["selection", "legacy", "artifact"])
def test_backup_verify_reconciliation_rechecks_device_and_digest(host_paths, change):
    from dataclasses import replace

    from robopark_host.commands import validate_typed_operation

    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    artifact = create(mount / "robopark-backups")
    adapter.backup_verify(str(uuid4()), _BACKUP_UUID)
    command = typed_request("backup-verify", backup_id=_BACKUP_UUID)
    command["authorization"] = authorization(command)
    operation = validate_typed_operation(command)
    assert adapter.reconcile(operation)["state"] == "succeeded"
    if change == "selection":
        selected = adapter.device_provider()[0]
        other = replace(selected, uuid=str(uuid4()), path="/dev/fake-other", mount_point="/mnt/other")
        create(host_paths.root / "mnt/other/robopark-backups")
        adapter.device_provider = lambda: (selected, other)
        adapter.usb_select(str(uuid4()), other)
    elif change == "legacy":
        path = host_paths.state / "backup-receipts" / f"{_BACKUP_UUID}.json"
        receipt = json.loads(path.read_text())
        receipt.pop("device_uuid", None)
        path.write_text(json.dumps(receipt))
    else:
        artifact.write_bytes(b"changed")
    assert adapter.reconcile(operation)["state"] == "failed"


def test_backup_verify_selection_swap_during_authentication_fails_closed(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.release import ReleaseError

    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    create(mount / "robopark-backups")
    real_verify = commands.verify_encrypted_backup

    def verify_and_switch(*args, **kwargs):
        result = real_verify(*args, **kwargs)
        (host_paths.state / "selected-usb.json").write_text(
            json.dumps({"schema": 1, "device_uuid": str(uuid4())})
        )
        return result

    monkeypatch.setattr(commands, "verify_encrypted_backup", verify_and_switch)
    with pytest.raises(ReleaseError):
        adapter.backup_verify(str(uuid4()), _BACKUP_UUID)
    assert not (host_paths.state / "backup-receipts").exists()


@pytest.mark.parametrize("receipt_kind", ["device-bound", "legacy-production"])
def test_device_bound_receipt_cannot_bypass_production_restore_guard(host_paths, receipt_kind):
    from robopark_host.commands import restore_encrypted_backup, verify_encrypted_backup
    from robopark_host.release import ReleaseError

    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    artifact = create(mount / "robopark-backups")
    adapter.backup_verify(str(uuid4()), _BACKUP_UUID)
    receipt = verify_encrypted_backup(artifact, recovery_key=b"c" * 32)
    if receipt_kind == "device-bound":
        receipt["device_uuid"] = _DEVICE_UUID
    else:
        receipt["backup_id"] = _BACKUP_UUID
    (host_paths.state / "selected-usb.json").write_text(
        json.dumps({"schema": 1, "device_uuid": str(uuid4())})
    )
    target = host_paths.root / "must-not-restore"
    with pytest.raises(ReleaseError, match="device_bound_restore_unavailable"):
        restore_encrypted_backup(artifact, target, recovery_key=b"c" * 32, verified=receipt)
    with pytest.raises(ReleaseError, match="capability_unavailable"):
        adapter.backup_restore(str(uuid4()), _BACKUP_UUID)
    assert not target.exists()


@pytest.mark.parametrize("unsafe", ["hardlink", "mode"])
def test_backup_verify_rejects_non_private_or_multi_link_artifact(host_paths, unsafe):
    from robopark_host.release import ReleaseError

    adapter, mount, create = _production_backup_verify_fixture(host_paths)
    artifact = create(mount / "robopark-backups")
    if unsafe == "hardlink":
        os.link(artifact, artifact.with_suffix(".alias"))
    else:
        artifact.chmod(0o644)

    with pytest.raises(ReleaseError, match="unsafe_backup_path"):
        adapter.backup_verify(str(uuid4()), _BACKUP_UUID)


@pytest.mark.parametrize("kind", ["backup-receipt", "selected-usb"])
def test_safe_adapter_reconcile_rejects_symlinked_private_lookup(host_paths, kind):
    from robopark_host.commands import validate_typed_operation

    adapter, _, _ = _production_backup_verify_fixture(host_paths)
    outside = host_paths.root / "outside-private"
    outside.mkdir()
    if kind == "backup-receipt":
        receipt = outside / f"{_BACKUP_UUID}.json"
        receipt.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "backup_id": _BACKUP_UUID,
                    "verified": True,
                    "sha256": "a" * 64,
                    "verified_at": 1,
                    "recovery_required": False,
                }
            )
        )
        receipt.chmod(0o600)
        (host_paths.state / "backup-receipts").symlink_to(
            outside, target_is_directory=True
        )
        command = typed_request("backup-verify", backup_id=_BACKUP_UUID)
    else:
        selected = outside / "selected-usb.json"
        selected.write_text(json.dumps({"schema": 1, "device_uuid": _DEVICE_UUID}))
        selected.chmod(0o600)
        (host_paths.state / "selected-usb.json").unlink()
        (host_paths.state / "selected-usb.json").symlink_to(selected)
        command = typed_request("usb-select", device_uuid=_DEVICE_UUID)
    command["authorization"] = authorization(command)

    assert adapter.reconcile(validate_typed_operation(command)) == {
        "state": "failed",
        "detail": {},
        "error": "manual_recovery_required",
    }


def test_safe_production_adapter_declares_exact_fail_closed_kinds():
    from robopark_host.commands import OperationKind, SafeProductionTypedHostEffects

    assert SafeProductionTypedHostEffects.unsupported_kinds() == {
        OperationKind.OTA_UPDATE,
        OperationKind.RELEASE_UPDATE,
        OperationKind.REINSTALL,
        OperationKind.ROLLBACK,
        OperationKind.PACKAGE_UPDATE,
        OperationKind.SERVICE_RESTART,
        OperationKind.REBOOT,
        OperationKind.BACKUP,
        OperationKind.BACKUP_RESTORE,
        OperationKind.CLEANUP_EXECUTE,
        OperationKind.USB_FORMAT,
    }


def test_typed_schema_is_closed_and_has_no_execution_escape():
    from robopark_host.commands import OperationKind, validate_typed_operation
    from robopark_host.release import ReleaseError

    valid = typed_request(OperationKind.PACKAGE_INSPECT.value, package="openssl")
    assert validate_typed_operation(valid).kind is OperationKind.PACKAGE_INSPECT
    for injected in (
        {**valid, "argv": ["sh", "-c", "id"]},
        {**valid, "command": "id"},
        {**valid, "kind": "shell"},
        {**valid, "package": "openssl; id"},
    ):
        with pytest.raises(ReleaseError, match="invalid_command"):
            validate_typed_operation(injected)


def test_api_typed_union_and_bridge_revalidate_consumed_authorization(tmp_path, host_paths, monkeypatch):
    from pydantic import TypeAdapter, ValidationError
    from robopark_api.ops_schemas import HostOperationIn
    from robopark_api.services.ops import host_bridge

    identity = str(uuid4())
    payload = {
        "operation_id": identity,
        "kind": "reboot",
        "confirmation": "REBOOT ROBOPARK",
        "capability_revision": revision_for(FakeHostEffects()),
    }
    adapter = TypeAdapter(HostOperationIn)
    assert str(adapter.validate_python(payload).operation_id) == identity
    with pytest.raises(ValidationError):
        adapter.validate_python({**payload, "argv": ["reboot"]})

    ops = tmp_path / "api-ops"
    root = host_paths.ops
    (root / "inbox").mkdir(parents=True)
    (root / "state").mkdir()
    (root / "public").mkdir()
    publish_test_capabilities(host_paths, FakeHostEffects(), monkeypatch)
    (root / "public/command-claim.json").write_text(
        json.dumps(
            {
                "job_id": str(uuid4()),
                "kind": "diagnostics",
                "actor_user_id": 1,
                "active": False,
            }
        )
    )
    with pytest.raises(host_bridge.BridgeError, match="authorization_required"):
        host_bridge.enqueue_typed_operation(
            ops,
            root,
            payload,
            7,
            "session-hash",
            authorization_consumed=False,
        )
    consumed = {
        "operation_id": identity,
        "operation_kind": "reboot",
        "actor_user_id": 7,
        "consumed": True,
    }
    job = host_bridge.enqueue_typed_operation(
        ops,
        root,
        payload,
        7,
        "session-hash",
        authorization_consumed=consumed,
    )
    request_value = json.loads((root / "inbox/approved.json").read_text())
    assert job.id == identity == request_value["job_id"]
    assert request_value["authorization"] == {
        "operation_id": identity,
        "operation_kind": "reboot",
        "actor_user_id": 7,
        "consumed": True,
        "validated_at": job.created_at,
    }


def test_usb_format_requires_uuid_safe_removable_device_and_double_confirmation(
    host_paths,
):
    from robopark_host.commands import BlockDevice, execute_typed_operation
    from robopark_host.release import ReleaseError

    identity = "00000000-0000-4000-8000-000000000001"
    phrase = f"FORMAT USB {identity}"
    command = typed_request(
        "usb-format",
        device_uuid=identity,
        confirmation=phrase,
        confirmation_repeat=phrase,
    )
    command["authorization"] = authorization(command)
    effects = FakeHostEffects()
    devices = [BlockDevice(identity, "/dev/fake-usb", removable=True)]

    result = execute_typed_operation(host_paths, command, effects, devices=devices)
    assert result["state"] == "succeeded"
    assert effects.calls == [("usb_format", command["job_id"], identity, "/dev/fake-usb")]
    progress = host_paths.state / "operation-progress" / f"{command['job_id']}.json"
    progress.unlink()  # simulate a crash after the terminal receipt but before projection
    assert execute_typed_operation(host_paths, command, effects, devices=devices) == result
    assert json.loads(progress.read_text())["phase"] == "succeeded"
    assert len(effects.calls) == 1
    collision = typed_request("package-inspect", package="openssl")
    collision["job_id"] = command["job_id"]
    collision["authorization"] = authorization(collision)
    with pytest.raises(ReleaseError, match="duplicate_operation_id"):
        execute_typed_operation(host_paths, collision, effects)

    for unsafe in (
        BlockDevice(identity, "/dev/fake-system", removable=False),
        BlockDevice(identity, "/dev/fake-mounted", removable=True, mounted=True),
        BlockDevice(identity, "/dev/fake-root", removable=True, root_device=True),
        BlockDevice(identity, "/dev/fake-data", removable=True, data_device=True),
    ):
        other = {**command, "job_id": str(uuid4())}
        other["authorization"] = authorization(other)
        with pytest.raises(ReleaseError, match="unsafe_usb_device"):
            execute_typed_operation(host_paths, other, effects, devices=[unsafe])


def test_destructive_operation_revalidates_consumed_authorization_and_phrase(host_paths):
    from robopark_host.commands import execute_typed_operation
    from robopark_host.release import ReleaseError

    command = typed_request("reboot", confirmation="REBOOT ROBOPARK")
    effects = FakeHostEffects()
    command.pop("authorization")
    with pytest.raises(ReleaseError, match="authorization_required"):
        execute_typed_operation(host_paths, command, effects)
    command["authorization"] = authorization(command)
    command["confirmation"] = "reboot robopark"
    with pytest.raises(ReleaseError, match="confirmation_required"):
        execute_typed_operation(host_paths, command, effects)
    assert effects.calls == []


def test_typed_release_error_is_terminal_and_same_id_is_not_retried(host_paths):
    from robopark_host.commands import execute_typed_operation

    command = typed_request("package-inspect", package="openssl")
    effects = FakeHostEffects(failure="package_inspect")

    first = execute_typed_operation(host_paths, command, effects)
    second = execute_typed_operation(host_paths, command, effects)

    assert first == second
    assert first["state"] == "failed"
    assert first["error"] == "simulated_host_failure"
    assert [call[0] for call in effects.calls] == ["package_inspect"]
    progress = json.loads(
        (host_paths.state / "operation-progress" / f"{command['job_id']}.json").read_text()
    )
    assert progress["phase"] == "failed"


def test_typed_resume_from_accepted_never_regresses_progress(host_paths):
    from robopark_host.commands import execute_typed_operation
    from robopark_host.state import write_operation_progress

    command = typed_request("package-inspect", package="openssl")
    write_operation_progress(host_paths, command["job_id"], "accepted", 0)
    effects = FakeHostEffects()

    assert execute_typed_operation(host_paths, command, effects)["state"] == "succeeded"
    assert [call[0] for call in effects.calls] == ["package_inspect"]


def test_typed_power_loss_after_dispatch_reconciles_without_repeating_effect(host_paths):
    from robopark_host.commands import execute_typed_operation, validate_typed_operation
    from robopark_host.state import atomic_write_json, write_operation_progress

    command = typed_request("reboot", confirmation="REBOOT ROBOPARK")
    command["authorization"] = authorization(command)
    operation = validate_typed_operation(command)
    checkpoint = host_paths.state / "typed-operation-dispatch" / f"{command['job_id']}.json"
    atomic_write_json(
        checkpoint,
        {"schema": 1, "request": operation.request, "state": "dispatched"},
    )
    write_operation_progress(host_paths, command["job_id"], "accepted", 0)
    write_operation_progress(host_paths, command["job_id"], "executing", 50)
    effects = FakeHostEffects(
        reconciliation={"state": "succeeded", "detail": {"scheduled": True}}
    )

    result = execute_typed_operation(host_paths, command, effects)

    assert result["state"] == "succeeded"
    assert effects.calls == [("reconcile", command["job_id"], "reboot")]


def test_consumer_resumes_durable_typed_checkpoint_after_authorization_window(host_paths):
    from robopark_host.commands import SystemTypedHostEffects, consume_commands
    from robopark_host.state import atomic_write_json, write_operation_progress

    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    command = typed_request("reboot", confirmation="REBOOT ROBOPARK")
    command["created_at"] = old
    command["authorization"] = authorization(command)
    atomic_write_json(host_paths.state / "command-request.json", command)
    atomic_write_json(
        host_paths.state / "typed-operation-intents" / f"{command['job_id']}.json",
        {"schema": 1, "request": command},
    )
    atomic_write_json(
        host_paths.state / "typed-operation-dispatch" / f"{command['job_id']}.json",
        {"schema": 1, "request": command, "state": "dispatched"},
    )
    write_operation_progress(host_paths, command["job_id"], "accepted", 0)
    write_operation_progress(host_paths, command["job_id"], "executing", 50)
    system = FakeHostEffects(
        reconciliation={"state": "succeeded", "detail": {"scheduled": True}}
    )

    code = consume_commands(
        host_paths,
        lambda *args, **kwargs: None,
        object(),
        typed_effects=SystemTypedHostEffects(host_paths, system=system),
    )

    assert code == 0
    assert system.calls == [("reconcile", command["job_id"], "reboot")]


def test_typed_dispatch_checkpoint_rejects_changed_request(host_paths):
    from robopark_host.commands import execute_typed_operation, validate_typed_operation
    from robopark_host.release import ReleaseError
    from robopark_host.state import atomic_write_json

    command = typed_request("package-inspect", package="openssl")
    operation = validate_typed_operation(command)
    checkpoint = host_paths.state / "typed-operation-dispatch" / f"{command['job_id']}.json"
    atomic_write_json(
        checkpoint,
        {"schema": 1, "request": operation.request, "state": "dispatched"},
    )
    changed = {**command, "package": "docker-ce"}

    with pytest.raises(ReleaseError, match="duplicate_operation_id"):
        execute_typed_operation(host_paths, changed, FakeHostEffects())


def test_encrypted_backup_never_contains_key_and_restore_requires_external_key(tmp_path):
    from robopark_host.commands import (
        create_encrypted_backup,
        restore_encrypted_backup,
        verify_encrypted_backup,
    )
    from robopark_host.release import ReleaseError

    source = tmp_path / "source"
    source.mkdir()
    (source / "db.dump").write_bytes(b"private database")
    key = b"k" * 32
    artifact = tmp_path / "backup.rpb"
    created = create_encrypted_backup(
        source,
        artifact,
        recovery_key=key,
        app_version="0.2.0-rc.6",
        schema_version="0050_media_action_dependency",
    )
    assert created["verified"] is False
    metadata = verify_encrypted_backup(artifact, recovery_key=key)
    raw = artifact.read_bytes()
    assert key not in raw and b"private database" not in raw
    target = tmp_path / "restored"
    with pytest.raises(ReleaseError, match="recovery_key_required"):
        restore_encrypted_backup(artifact, target, recovery_key=None, verified=metadata)
    restored = restore_encrypted_backup(artifact, target, recovery_key=key, verified=metadata)
    assert restored["verified"] is True
    assert (target / "db.dump").read_bytes() == b"private database"


def test_restore_rejects_unverified_or_incompatible_backup(tmp_path):
    from robopark_host.commands import (
        create_encrypted_backup,
        restore_encrypted_backup,
        verify_encrypted_backup,
    )
    from robopark_host.release import ReleaseError

    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"x")
    artifact = tmp_path / "backup.rpb"
    key = b"r" * 32
    create_encrypted_backup(
        source,
        artifact,
        recovery_key=key,
        app_version="0.2.0-rc.6",
        schema_version="0050_media_action_dependency",
    )
    receipt = verify_encrypted_backup(artifact, recovery_key=key)
    with pytest.raises(ReleaseError, match="verified_backup_required"):
        restore_encrypted_backup(artifact, tmp_path / "no", recovery_key=key, verified=None)
    wrong_target = tmp_path / "wrong-key"
    with pytest.raises(ReleaseError, match="backup_integrity_failed"):
        restore_encrypted_backup(
            artifact,
            wrong_target,
            recovery_key=b"w" * 32,
            verified=receipt,
        )
    assert not wrong_target.exists()
    with pytest.raises(ReleaseError, match="backup_version_incompatible"):
        restore_encrypted_backup(
            artifact,
            tmp_path / "bad",
            recovery_key=key,
            verified={**receipt, "app_version": "9.0.0"},
        )


@pytest.mark.parametrize(
    "limits",
    [
        {"max_members": 1},
        {"max_member_bytes": 2},
        {"max_total_bytes": 4},
        {"max_compression_ratio": 1},
        {"max_metadata_bytes": 1},
    ],
)
def test_backup_verification_rejects_bounded_archive_limits(tmp_path, limits):
    from robopark_host.commands import (
        BackupArchiveLimits,
        create_encrypted_backup,
        verify_encrypted_backup,
    )
    from robopark_host.release import ReleaseError

    source = tmp_path / "source"
    source.mkdir()
    (source / "one").write_bytes(b"a" * 64)
    (source / "two").write_bytes(b"b" * 64)
    artifact = tmp_path / "backup.rpb"
    key = b"l" * 32
    create_encrypted_backup(
        source,
        artifact,
        recovery_key=key,
        app_version="0.2.0-rc.6",
        schema_version="0050_media_action_dependency",
    )

    with pytest.raises(ReleaseError, match="backup_archive_limit"):
        verify_encrypted_backup(
            artifact,
            recovery_key=key,
            archive_limits=BackupArchiveLimits(**limits),
        )


def test_backup_verification_streams_without_extractall(tmp_path, monkeypatch):
    from robopark_host.commands import create_encrypted_backup, verify_encrypted_backup

    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"small")
    artifact = tmp_path / "backup.rpb"
    key = b"s" * 32
    create_encrypted_backup(
        source,
        artifact,
        recovery_key=key,
        app_version="0.2.0-rc.6",
        schema_version="0050_media_action_dependency",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("extractall must never be used")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden)
    assert verify_encrypted_backup(artifact, recovery_key=key)["verified"] is True


@pytest.mark.parametrize(
    ("source_files", "limits"),
    [
        ({"one": b"1", "two": b"2"}, {"max_members": 1}),
        ({"one": b"1"}, {"max_central_directory_bytes": 1}),
    ],
)
def test_backup_zip_directory_bounds_reject_before_zipfile_allocation(
    tmp_path, monkeypatch, source_files, limits
):
    from robopark_host.commands import (
        BackupArchiveLimits,
        create_encrypted_backup,
        verify_encrypted_backup,
    )
    from robopark_host.release import ReleaseError

    source = tmp_path / "source"
    source.mkdir()
    for name, body in source_files.items():
        (source / name).write_bytes(body)
    artifact = tmp_path / "backup.rpb"
    key = b"e" * 32
    create_encrypted_backup(
        source,
        artifact,
        recovery_key=key,
        app_version="0.2.0-rc.6",
        schema_version="0050_media_action_dependency",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("ZipFile must not be constructed before EOCD bounds")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden)
    with pytest.raises(ReleaseError, match="backup_archive_limit"):
        verify_encrypted_backup(
            artifact,
            recovery_key=key,
            archive_limits=BackupArchiveLimits(**limits),
        )


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ((1, 0, 0, 0, 0, 0, 0), "backup_manifest_invalid"),
        ((0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0), "backup_archive_limit"),
        ((0, 0, 1, 1, 1, 100, 0), "backup_manifest_invalid"),
    ],
)
def test_zip_preflight_rejects_multidisk_zip64_and_bad_offsets(tmp_path, fields, error):
    from robopark_host.commands import BackupArchiveLimits, _preflight_zip_directory
    from robopark_host.release import ReleaseError

    payload = tmp_path / "payload.zip"
    payload.write_bytes(struct.pack("<4s4H2LH", b"PK\x05\x06", *fields))

    with pytest.raises(ReleaseError, match=error):
        _preflight_zip_directory(payload, BackupArchiveLimits())


def test_zip_preflight_counts_actual_central_headers_not_only_eocd(tmp_path):
    from robopark_host.commands import BackupArchiveLimits, _preflight_zip_directory
    from robopark_host.release import ReleaseError

    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("one", b"1")
        archive.writestr("two", b"2")
    raw = bytearray(payload.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    struct.pack_into("<H", raw, eocd + 8, 1)
    struct.pack_into("<H", raw, eocd + 10, 1)
    payload.write_bytes(raw)

    with pytest.raises(ReleaseError, match="backup_archive_limit"):
        _preflight_zip_directory(payload, BackupArchiveLimits(max_members=1))


def test_diagnostics_consumes_once_and_exports_readable_zip(host_paths, monkeypatch):
    from robopark_host import commands

    command = request(host_paths)
    monkeypatch.setattr(
        commands,
        "run_doctor",
        lambda *args: DiagnosticReport(
            [CheckResult("tuna_inactive", "failed", "Tuna unavailable", "restart_tuna")]
        ),
    )
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return CommandResult(stdout='{"MESSAGE":"secret-value","_CMDLINE":"secret-value"}')

    assert commands.consume_commands(host_paths, runner, object()) == 0
    assert not (host_paths.ops / "inbox/approved.json").exists()
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["job_id"] == command["job_id"]
    assert result["state"] == "succeeded"
    artifact = host_paths.ops / "public/artifacts" / result["artifact"]
    assert artifact.name == command["job_id"] + ".zip"
    assert artifact.stat().st_mode & 0o777 == 0o644
    with zipfile.ZipFile(artifact) as archive:
        assert "secret-value" not in "".join(archive.read(n).decode() for n in archive.namelist())
    before = len(calls)
    assert commands.consume_commands(host_paths, runner, object()) == 0
    assert len(calls) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "shell"},
        {"actor_user_id": 0},
        {"artifact": "/etc/passwd"},
        {"argv": ["rm", "-rf", "/"]},
        {"created_at": "2026-09-07T00:00:00"},
    ],
)
def test_consumer_rejects_noncanonical_command_without_running(host_paths, changes):
    from robopark_host import commands

    request(host_paths, **changes)
    assert (
        commands.consume_commands(
            host_paths, lambda *args, **kwargs: pytest.fail("invalid command executed"), object()
        )
        == 1
    )
    assert not (host_paths.ops / "inbox/approved.json").exists()


def test_repair_uses_fixed_allowlist_and_publishes_before_after(host_paths, monkeypatch):
    from robopark_host import commands

    request(host_paths, "repair")
    reports = iter(
        [
            DiagnosticReport(
                [
                    CheckResult("tuna_inactive", "failed", "failed", "restart_tuna"),
                    CheckResult("database_unavailable", "failed", "db"),
                ]
            ),
            DiagnosticReport(
                [
                    CheckResult("tuna_inactive", "ok", "ok"),
                    CheckResult("database_unavailable", "failed", "db"),
                ]
            ),
        ]
    )
    monkeypatch.setattr(commands, "run_doctor", lambda *args: next(reports))
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return CommandResult()

    commands.consume_commands(host_paths, runner, object())
    assert calls == [["systemctl", "restart", "robopark-tuna.service"]]
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["performed"] == ["restart_tuna"]
    assert result["before"][0]["status"] == "failed"
    assert result["after"][0]["status"] == "ok"


def test_path_trigger_runs_bounded_root_consumer():
    root = Path(__file__).resolve().parents[2] / "deploy/systemd"
    path = configparser.ConfigParser(interpolation=None)
    path.read(root / "robopark-commands.path")
    assert path["Path"]["PathExists"] == "/var/lib/robopark/ops/inbox/approved.json"
    service = configparser.ConfigParser(interpolation=None)
    service.read(root / path["Path"]["Unit"])
    assert (
        service["Service"]["ExecStart"]
        == "/usr/bin/python3 -I /opt/robopark/host-tools/robopark consume"
    )
    assert service["Service"].get("User", "root") == "root"
    assert int(service["Service"]["TimeoutStartSec"]) <= 18000
    assert service["Service"]["ProtectSystem"] == "strict"


def test_update_consumer_calls_launcher_once_without_doctor(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(host_paths, "update", artifact="update-test.zip")
    calls = []

    def launcher(paths, path, runner):
        calls.append(json.loads(path.read_text()))
        atomic_write_json(
            paths.ops / "public/rebuild.result",
            {"job_id": command["job_id"], "ok": True, "error": None},
            mode=0o644,
        )
        path.unlink()
        return 0

    monkeypatch.setattr("robopark_host.launcher.launch_update", launcher)
    monkeypatch.setattr(
        commands, "run_doctor", lambda *args: pytest.fail("update consumed as diagnostics")
    )
    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert calls == [command]


def test_fresh_update_is_not_superseded_by_previous_success(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    previous = str(uuid4())
    atomic_write_json(
        host_paths.state / "updater-journal.json",
        {"job_id": previous, "phase": "succeeded"},
    )
    atomic_write_json(
        host_paths.ops / "public/rebuild.result",
        {"job_id": previous, "ok": True, "error": None},
        mode=0o644,
    )
    command = request(host_paths, "update", artifact="update-next.zip")
    launched = []

    def launcher(paths, path, runner):
        launched.append(json.loads(path.read_text()))
        atomic_write_json(
            paths.ops / "public/rebuild.result",
            {"job_id": command["job_id"], "ok": True, "error": None},
            mode=0o644,
        )
        return 0

    monkeypatch.setattr("robopark_host.launcher.launch_update", launcher)

    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert launched == [command]


def test_repair_does_not_reexecute_claimed_command_after_crash(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(host_paths, "repair")
    atomic_write_json(host_paths.state / "command-request.json", command)
    monkeypatch.setattr(commands, "run_doctor", lambda *args: pytest.fail("repair replayed"))
    assert commands.consume_commands(host_paths, None, None) == 1
    # The API slot from a crash before unlink must also be retired.
    assert not (host_paths.ops / "inbox/approved.json").exists()
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["error"] == "command_interrupted"


def test_scheduled_doctor_publishes_allowlisted_readable_health(host_paths, monkeypatch):
    from robopark_host.doctor import run_doctor
    from test_diagnostic_review_regressions import ContractHttp, ReviewRunner

    report = run_doctor(host_paths, ReviewRunner("[]"), ContractHttp())
    path = host_paths.ops / "public/system-health.json"
    assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o644
    value = json.loads(path.read_text())
    assert value["generated_at"] == report.created_at
    assert set(value) == {
        "version",
        "git_sha",
        "generated_at",
        "overall",
        "checks",
        "update",
        "last_backup",
    }


def test_bundle_validates_allowed_metadata_values_not_only_field_names(host_paths, tmp_path):
    from robopark_host.bundle import create_diagnostic_bundle

    release = host_paths.releases / "1.0.0"
    release.mkdir(parents=True)
    (release / "manifest.json").write_text('{"app_version":"/root/LEAK","git_sha":"token=LEAK"}')
    host_paths.current.symlink_to(release)

    def runner(argv, **kwargs):
        if argv[0] == "journalctl":
            return CommandResult(
                stdout=json.dumps(
                    {
                        "__REALTIME_TIMESTAMP": "LEAK",
                        "PRIORITY": "LEAK",
                        "_SYSTEMD_UNIT": "LEAK",
                        "MESSAGE_ID": "token=LEAK",
                    }
                )
            )
        return CommandResult(stdout="[]")

    artifact = create_diagnostic_bundle(
        host_paths, DiagnosticReport([]), runner, tmp_path / "safe.zip"
    )
    with zipfile.ZipFile(artifact) as archive:
        assert "LEAK" not in "".join(archive.read(name).decode() for name in archive.namelist())


def test_installer_installs_command_trigger_as_part_of_atomic_unit_set(host_paths):
    import runpy

    module = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "deploy/installer/lib/install-services.py")
    )
    release = host_paths.releases / "1.0.0"
    units = release / "deploy/systemd"
    units.mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "deploy/systemd"
    for name in module["UNITS"]:
        (units / name).write_bytes((source / name).read_bytes())
    tmpfiles = release / "deploy/tmpfiles.d"
    tmpfiles.mkdir(parents=True)
    (tmpfiles / "robopark.conf").write_bytes(
        (Path(__file__).resolve().parents[2] / "deploy/tmpfiles.d/robopark.conf").read_bytes()
    )
    host_paths.current.symlink_to(release)
    module["install_units"](host_paths.root)
    assert (host_paths.root / "etc/systemd/system/robopark-commands.path").is_file()
    assert (host_paths.root / "etc/systemd/system/robopark-commands.service").is_file()
    assert (host_paths.root / "etc/tmpfiles.d/robopark.conf").is_file()


def test_completed_large_diagnostics_receipt_prevents_reexecution(host_paths, monkeypatch):
    from robopark_host import commands

    command = request(host_paths)
    report = DiagnosticReport([CheckResult(f"check{i}", "ok", "healthy" * 30) for i in range(26)])
    monkeypatch.setattr(commands, "run_doctor", lambda *args: report)
    assert commands.consume_commands(host_paths, lambda *args, **kwargs: CommandResult(), None) == 0
    (host_paths.ops / "inbox/approved.json").write_text(json.dumps(command))
    monkeypatch.setattr(
        commands, "run_doctor", lambda *args: pytest.fail("completed diagnostic replayed")
    )
    assert commands.consume_commands(host_paths, None, None) == 0


def test_bundle_drops_nested_compose_metadata(host_paths, tmp_path):
    from robopark_host.bundle import create_diagnostic_bundle

    def runner(argv, **kwargs):
        return CommandResult(
            stdout=json.dumps(
                {
                    "Service": "api",
                    "Name": {"cookie": "LEAK"},
                    "State": "/root/LEAK",
                    "Health": "LEAK",
                    "ExitCode": {"token": "LEAK"},
                }
            )
        )

    artifact = create_diagnostic_bundle(
        host_paths, DiagnosticReport([]), runner, tmp_path / "safe.zip"
    )
    with zipfile.ZipFile(artifact) as archive:
        assert "LEAK" not in archive.read("compose-services.json").decode()


def test_claimed_repair_after_a_day_fails_closed_without_replay(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(host_paths, "repair", created_at="2020-01-01T00:00:00+00:00")
    atomic_write_json(host_paths.state / "command-request.json", command)
    monkeypatch.setattr(commands, "run_doctor", lambda *args: pytest.fail("old repair replayed"))
    assert commands.consume_commands(host_paths, None, None) == 1
    assert not (host_paths.state / "command-request.json").exists()
    assert (
        json.loads((host_paths.ops / "public/command-result.json").read_text())["error"]
        == "command_interrupted"
    )


def test_command_service_retries_crashed_consumer_with_bounded_backoff():
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(Path(__file__).resolve().parents[2] / "deploy/systemd/robopark-commands.service")
    assert parser["Service"]["Restart"] == "on-failure"
    assert 1 <= int(parser["Service"]["RestartSec"]) <= 30
    assert parser["Unit"]["StartLimitIntervalSec"] == "0"


def test_public_health_reports_backup_time_without_private_backup_fields(host_paths):
    from robopark_host.commands import publish_health
    from robopark_host.state import atomic_write_json

    atomic_write_json(
        host_paths.state / "last-backup.json",
        {
            "status": "success",
            "completed_at": "2026-09-07T00:00:00+00:00",
            "path": "/root/LEAK",
            "token": "LEAK",
        },
    )
    publish_health(host_paths, DiagnosticReport([]))
    raw = (host_paths.ops / "public/system-health.json").read_text()
    assert json.loads(raw)["last_backup"] == {
        "status": "success",
        "completed_at": "2026-09-07T00:00:00+00:00",
    }
    assert "LEAK" not in raw


def test_installer_enables_and_starts_approved_command_trigger(tmp_path):
    import subprocess

    script = Path(__file__).resolve().parents[2] / "deploy/installer/lib/services.sh"
    logfile = tmp_path / "systemctl.log"
    result = subprocess.run(
        [
            "sh",
            "-c",
            """
        . "$1"
        INSTALLER_DIR=/fixture
        ROBOPARK_OPT=/fixture
        ROBOPARK_ROOT=/fixture
        ROBOPARK_ETC="$TASK_ETC"
        mkdir -p "$ROBOPARK_ETC"
        printf "TUNA_SUBDOMAIN='park'\nTUNA_LOCATION='ru'\nTUNA_DOMAIN=''\n" > "$ROBOPARK_ETC/tuna.env"
        python3() { :; }
        curl() { return 0; }
        systemctl() { printf '%s\\n' "$*" >> "$TASK_LOG"; }
        die() { exit 1; }
        install_services
        start_host_automation
    """,
            "installer-test",
            str(script),
        ],
        env={"TASK_LOG": str(logfile), "TASK_ETC": str(tmp_path / "etc")},
        capture_output=True,
    )
    assert result.returncode == 0
    commands = [line.split() for line in logfile.read_text().splitlines()]
    assert any(line[0] == "enable" and "robopark-commands.path" in line for line in commands)
    assert any(line[0] == "start" and "robopark-commands.path" in line for line in commands)


def test_expired_claimed_update_recovers_then_publishes_bounded_rejection(host_paths, monkeypatch):
    from types import SimpleNamespace

    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(
        host_paths, "update", artifact="update-approved.zip", created_at="2020-01-01T00:00:00+00:00"
    )
    atomic_write_json(host_paths.state / "command-request.json", command)
    recovered = []
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *args: recovered.append(True) or SimpleNamespace(state="idle"),
    )
    monkeypatch.setattr(
        "robopark_host.launcher.launch_update",
        lambda *args: pytest.fail("expired request executed"),
    )
    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 1
    assert recovered == [True]
    result = json.loads((host_paths.ops / "public/rebuild.result").read_text())
    assert result == {"job_id": command["job_id"], "ok": False, "error": "request_expired"}
    assert not (host_paths.state / "command-request.json").exists()
    assert not (host_paths.ops / "inbox/approved.json").exists()


def test_successful_newer_local_update_retires_superseded_host_request(host_paths, monkeypatch):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = request(
        host_paths,
        "update",
        artifact="update-old.zip",
        created_at=(datetime.now(UTC) - timedelta(seconds=30)).isoformat(),
    )
    atomic_write_json(host_paths.state / "command-request.json", command)
    atomic_write_json(
        host_paths.ops / "public/command-claim.json",
        {**{key: command[key] for key in ("job_id", "kind", "actor_user_id")}, "active": True},
        mode=0o644,
    )
    successor = str(uuid4())
    candidate = host_paths.releases / ("0.1.35-" + successor)
    candidate.mkdir(parents=True)
    host_paths.current.symlink_to(candidate)
    atomic_write_json(
        host_paths.state / "updater-journal.json",
        {"job_id": successor, "candidate": candidate.name, "phase": "succeeded"},
    )
    atomic_write_json(
        host_paths.ops / "public/rebuild.result",
        {"job_id": successor, "ok": True, "error": None},
        mode=0o644,
    )
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *args: pytest.fail("superseded request entered update recovery"),
    )
    monkeypatch.setattr(
        "robopark_host.launcher.launch_update",
        lambda *args: pytest.fail("superseded update relaunched"),
    )

    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 1

    result = json.loads((host_paths.ops / "public/rebuild.result").read_text())
    assert result == {"job_id": command["job_id"], "ok": False, "error": "request_superseded"}
    assert not (host_paths.state / "command-request.json").exists()
    assert json.loads((host_paths.ops / "public/command-claim.json").read_text())["active"] is False


def test_resumed_update_after_previous_success_is_not_superseded(host_paths, monkeypatch):
    from types import SimpleNamespace

    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    previous = str(uuid4())
    candidate = host_paths.releases / ("0.1.33-" + previous)
    candidate.mkdir(parents=True)
    host_paths.current.symlink_to(candidate)
    journal_path = host_paths.state / "updater-journal.json"
    atomic_write_json(
        journal_path,
        {"job_id": previous, "candidate": candidate.name, "phase": "succeeded"},
    )
    atomic_write_json(
        host_paths.ops / "public/rebuild.result",
        {"job_id": previous, "ok": True, "error": None},
        mode=0o644,
    )
    command = request(
        host_paths,
        "update",
        artifact="update-next.zip",
        created_at=(
            datetime.fromtimestamp(host_paths.current.lstat().st_mtime, UTC) + timedelta(seconds=1)
        ).isoformat(),
    )
    atomic_write_json(host_paths.state / "command-request.json", command)
    # Reconciliation may rewrite the already-successful journal after this request.
    later = datetime.fromisoformat(command["created_at"]).timestamp() + 1
    os.utime(journal_path, (later, later))
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *args: SimpleNamespace(state="idle"),
    )

    def launcher(paths, path, runner):
        atomic_write_json(
            paths.ops / "public/rebuild.result",
            {"job_id": command["job_id"], "ok": True, "error": None},
            mode=0o644,
        )
        return 0

    monkeypatch.setattr("robopark_host.launcher.launch_update", launcher)

    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert json.loads((host_paths.ops / "public/rebuild.result").read_text()) == {
        "job_id": command["job_id"],
        "ok": True,
        "error": None,
    }
    assert not (host_paths.state / "command-request.json").exists()


def test_expired_approved_diagnostics_publishes_failure_without_work(host_paths, monkeypatch):
    from robopark_host import commands

    command = request(host_paths, "diagnostics", created_at="2020-01-01T00:00:00+00:00")
    monkeypatch.setattr(
        commands, "run_doctor", lambda *args: pytest.fail("expired request executed")
    )
    assert commands.consume_commands(host_paths, None, None) == 1
    result = json.loads((host_paths.ops / "public/command-result.json").read_text())
    assert result["job_id"] == command["job_id"]
    assert result["state"] == "failed"
    assert result["error"] == "request_expired"
    assert not (host_paths.ops / "inbox/approved.json").exists()


def test_trigger_does_not_rate_limit_four_successful_commands(host_paths, monkeypatch):
    from robopark_host import commands

    unit = configparser.ConfigParser(interpolation=None)
    unit.read(Path(__file__).resolve().parents[2] / "deploy/systemd/robopark-commands.service")
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"
    monkeypatch.setattr(commands, "run_doctor", lambda *args: DiagnosticReport([]))
    for _ in range(4):
        command = request(host_paths)
        assert (
            commands.consume_commands(host_paths, lambda *args, **kwargs: CommandResult(), None)
            == 0
        )
        assert (
            json.loads((host_paths.ops / "public/command-result.json").read_text())["job_id"]
            == command["job_id"]
        )


def test_crash_retry_budget_is_per_command_and_survives_process_restarts(host_paths, monkeypatch):
    from types import SimpleNamespace

    from robopark_host import commands

    request(host_paths, "update", artifact="update-test.zip")
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *args: SimpleNamespace(state="idle"),
    )
    launches = []

    def crash(*args):
        launches.append(True)
        raise SystemExit("crash")

    monkeypatch.setattr("robopark_host.launcher.launch_update", crash)
    for _ in range(3):
        with pytest.raises(SystemExit):
            commands.consume_commands(host_paths, None, None, update_runner=object())
    assert commands.consume_commands(host_paths, None, None, update_runner=object()) == 0
    assert len(launches) == 3
    assert json.loads((host_paths.ops / "public/maintenance.json").read_text())["enabled"] is True
    assert (
        json.loads((host_paths.ops / "public/host-status.json").read_text())["error"]
        == "manual_recovery_required"
    )


def test_deeply_nested_inbox_is_rejected_and_removed(host_paths, monkeypatch):
    from robopark_host import commands

    request(host_paths)
    (host_paths.ops / "inbox/approved.json").write_text("[" * 1500 + "0" + "]" * 1500)
    monkeypatch.setattr(
        commands.json, "loads", lambda *args, **kwargs: (_ for _ in ()).throw(RecursionError())
    )
    assert commands.consume_commands(host_paths, None, None) == 1
    assert not (host_paths.ops / "inbox/approved.json").exists()


def test_production_typed_schema_rejects_legacy_request_without_capability_revision():
    from robopark_host.commands import validate_typed_operation
    from robopark_host.release import ReleaseError

    command = typed_request("package-inspect", package="openssl")
    command.pop("capability_revision")
    command["authorization"] = authorization(command)
    with pytest.raises(ReleaseError, match="invalid_command"):
        validate_typed_operation(command)


def test_resumed_legacy_typed_request_fails_before_effect(host_paths):
    from robopark_host import commands
    from robopark_host.state import atomic_write_json

    command = typed_request("package-inspect", package="openssl")
    command.pop("capability_revision")
    command["authorization"] = authorization(command)
    atomic_write_json(host_paths.state / "command-request.json", command)
    effects = FakeHostEffects()
    assert commands.consume_commands(host_paths, None, None, typed_effects=effects) == 1
    assert effects.calls == []
