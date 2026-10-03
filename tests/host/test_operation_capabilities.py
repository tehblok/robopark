"""Capability discovery must describe the injected executor without running it."""

import json
from uuid import uuid4

import pytest
from robopark_host.commands import SafeProductionTypedHostEffects, TypedHostEffects

BOOT_ID = "00000000-0000-4000-8000-000000000010"
SAFE_KINDS = {
    "ota-update", "package-inspect", "cleanup-preview", "cleanup-execute", "diagnostics",
    "docker-image-preview", "docker-image-execute", "builder-cache-preview",
    "builder-cache-execute", "usb-discover", "usb-select", "package-update",
    "service-restart", "reboot", "usb-format",
}
UNAVAILABLE_KINDS = {
    "rollback", "backup", "backup-restore",
}


@pytest.fixture
def capability_host(host_paths, monkeypatch):
    from robopark_api.services.ops import host_bridge

    boot = host_paths.root / "proc/sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True, exist_ok=True)
    boot.write_text(BOOT_ID + "\n")
    monkeypatch.setattr(host_bridge, "_host_boot_id", lambda: BOOT_ID, raising=False)
    return host_paths


def test_idle_production_consumer_marks_missing_backup_key_context_unavailable(capability_host):
    from robopark_api.services.ops import host_bridge
    from robopark_host.cli import _consume_handler

    assert _consume_handler(capability_host) == 0
    report = host_bridge.operation_capabilities(capability_host.ops)
    assert report.state == "ready"
    assert {kind.value for kind, value in report.operations.items() if value.available} == SAFE_KINDS
    reasons = {
        kind.value: value.unavailable_reason
        for kind, value in report.operations.items() if not value.available
    }
    assert reasons == {
        **dict.fromkeys(UNAVAILABLE_KINDS, "context_unavailable"),
        "backup-verify": "context_unavailable",
    }
    assert (capability_host.ops / "public/operation-context.json").is_file()
    assert not (capability_host.ops / "inbox/approved.json").exists()
    assert not (capability_host.etc / "backup-recovery.key").exists()


def test_consumer_capabilities_follow_injected_adapter_and_are_replaced(capability_host):
    from robopark_api.services.ops import host_bridge
    from robopark_host.commands import SystemTypedHostEffects, consume_commands

    class InspectionOnly(TypedHostEffects):
        supported_kinds = frozenset({"package-inspect"})

    effects = SystemTypedHostEffects(capability_host, system=InspectionOnly())
    assert consume_commands(capability_host, None, None, typed_effects=effects) == 0
    report = host_bridge.operation_capabilities(capability_host.ops)
    assert {kind.value for kind, item in report.operations.items() if item.available} == {"package-inspect"}
    assert consume_commands(capability_host, None, None, typed_effects=TypedHostEffects()) == 0
    assert not any(item.available for item in host_bridge.operation_capabilities(capability_host.ops).operations.values())


def test_missing_diagnostic_dependencies_are_not_advertised(capability_host):
    from robopark_api.services.ops import host_bridge
    from robopark_host.commands import consume_commands

    effects = SafeProductionTypedHostEffects(capability_host)
    consume_commands(capability_host, None, None, typed_effects=effects)
    assert not host_bridge.operation_capabilities(capability_host.ops).operations["diagnostics"].available
    assert not host_bridge.operation_capabilities(capability_host.ops).operations["docker-image-preview"].available
    assert not host_bridge.operation_capabilities(capability_host.ops).operations["builder-cache-preview"].available


@pytest.mark.parametrize("drift", ["adapter", "boot"])
def test_queued_request_cannot_execute_after_capability_drift(capability_host, drift):
    from robopark_api.services.ops import host_bridge
    from robopark_host.commands import consume_commands

    class InspectionOnly(TypedHostEffects):
        supported_kinds = frozenset({"package-inspect"})

        def package_inspect(self, *args):
            pytest.fail("capability drift reached the effect")

    effects = InspectionOnly()
    consume_commands(capability_host, None, None, typed_effects=effects)
    revision = host_bridge.operation_capabilities(capability_host.ops).revision
    (capability_host.ops / "inbox").mkdir(exist_ok=True)
    (capability_host.ops / "public").mkdir(exist_ok=True)
    (capability_host.ops / "public/command-claim.json").write_text(json.dumps({
        "job_id": str(uuid4()), "kind": "diagnostics", "actor_user_id": 1, "active": False,
    }))
    identity = str(uuid4())
    host_bridge.enqueue_typed_operation(
        capability_host.var / "api-ops", capability_host.ops,
        {
            "operation_id": identity, "kind": "package-inspect", "package": "openssl",
            "capability_revision": revision,
            "confirmation": "ЗАПУСТИТЬ PACKAGE-INSPECT",
        },
        7, "session", authorization_consumed={
            "operation_id": identity, "operation_kind": "package-inspect",
            "actor_user_id": 7, "consumed": True,
        },
    )
    if drift == "adapter":
        effects = TypedHostEffects()
    else:
        (capability_host.root / "proc/sys/kernel/random/boot_id").write_text(str(uuid4()) + "\n")
    assert consume_commands(capability_host, None, None, typed_effects=effects) == 1
    result = json.loads((capability_host.ops / "public/command-result.json").read_text())
    assert result["state"] == "failed"
    assert result["error"] in {"capability_unavailable", "capabilities_changed"}
    assert not (capability_host.state / "typed-operation-dispatch" / f"{identity}.json").exists()


def test_periodic_watchdog_refreshes_same_production_adapter(capability_host, monkeypatch):
    from types import SimpleNamespace

    from robopark_api.services.ops import host_bridge
    from robopark_host import cli

    monkeypatch.setattr(cli, "run_watchdog", lambda *args: SimpleNamespace(
        consecutive_failures=0, restarted=False, busy=False,
    ))
    assert cli._watchdog_handler(capability_host) == 0
    report = host_bridge.operation_capabilities(capability_host.ops)
    assert report.state == "ready"
    assert {kind.value for kind, item in report.operations.items() if item.available} == SAFE_KINDS
    assert not (capability_host.etc / "backup-recovery.key").exists()


def test_operation_context_projects_only_bounded_safe_choices(capability_host):
    from robopark_host.commands import BlockDevice
    from robopark_host.operation_capabilities import publish_operation_context
    from robopark_host.state import atomic_write_json

    update_id = "00000000-0000-4000-8000-000000000001"
    current = capability_host.releases / f"0.2.0-{update_id}"
    previous = capability_host.releases / "0.1.9-previous"
    current.mkdir(parents=True)
    previous.mkdir()
    capability_host.current.symlink_to(current)
    capability_host.previous.symlink_to(previous)
    metadata = capability_host.state / "ota-runtime" / f"{update_id}.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({
        "schema": 1,
        "operation_id": update_id,
        "candidate": current.name,
        "current": previous.name,
    }))
    (capability_host.ops / "rollbacks" / update_id).mkdir(parents=True)
    key = capability_host.etc / "backup-recovery.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    device = BlockDevice(
        "00000000-0000-4000-8000-000000000001",
        "/dev/sdz1",
        removable=True,
        mounted=True,
        mount_point="/mnt/usb",
    )
    atomic_write_json(
        capability_host.state / "selected-usb.json",
        {"schema": 1, "device_uuid": device.uuid},
    )
    effects = SafeProductionTypedHostEffects(
        capability_host,
        runner=lambda *args, **kwargs: None,
        device_provider=lambda: (device,),
    )

    value = publish_operation_context(capability_host, effects)

    assert value["rollback_release"] == previous.name
    assert value["selected_device_uuid"] == device.uuid
    assert value["packages"] == [
        "containerd.io", "docker-ce", "docker-ce-cli", "openssl",
        "python3-cryptography",
    ]
    assert value["services"] == ["docker.service", "robopark-tuna.service", "robopark.service"]
    assert value["devices"] == [
        {"device_uuid": device.uuid, "removable": True, "mounted": True}
    ]
    assert value["backups"] == []
    assert set(value) == {
        "schema", "boot_id", "generated_at", "valid_for_seconds",
        "selected_device_uuid", "rollback_release", "packages", "services",
        "devices", "backups",
    }
