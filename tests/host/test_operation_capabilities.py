"""Capability discovery must describe the injected executor without running it."""

import json
from uuid import uuid4

import pytest
from robopark_host.commands import SafeProductionTypedHostEffects, TypedHostEffects

BOOT_ID = "00000000-0000-4000-8000-000000000010"
SAFE_KINDS = {
    "package-inspect", "backup-verify", "cleanup-preview", "diagnostics",
    "usb-discover", "usb-select",
}
UNAVAILABLE_KINDS = {
    "release-update", "reinstall", "rollback", "package-update", "service-restart",
    "reboot", "backup", "backup-restore", "cleanup-execute", "usb-format",
}


@pytest.fixture
def capability_host(host_paths, monkeypatch):
    from robopark_api.services.ops import host_bridge

    boot = host_paths.root / "proc/sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True)
    boot.write_text(BOOT_ID + "\n")
    monkeypatch.setattr(host_bridge, "_host_boot_id", lambda: BOOT_ID, raising=False)
    return host_paths


def test_idle_production_consumer_publishes_only_its_six_supported_kinds(capability_host):
    from robopark_api.services.ops import host_bridge
    from robopark_host.cli import _consume_handler

    assert _consume_handler(capability_host) == 0
    report = host_bridge.operation_capabilities(capability_host.ops)
    assert report.state == "ready"
    assert {kind.value for kind, value in report.operations.items() if value.available} == SAFE_KINDS
    assert {
        kind.value: value.unavailable_reason
        for kind, value in report.operations.items() if not value.available
    } == dict.fromkeys(UNAVAILABLE_KINDS, "capability_unavailable")
    assert not (capability_host.ops / "inbox/approved.json").exists()


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
    (capability_host.ops / "inbox").mkdir(exist_ok=True)
    (capability_host.ops / "public").mkdir(exist_ok=True)
    (capability_host.ops / "public/command-claim.json").write_text(json.dumps({
        "job_id": str(uuid4()), "kind": "diagnostics", "actor_user_id": 1, "active": False,
    }))
    identity = str(uuid4())
    host_bridge.enqueue_typed_operation(
        capability_host.var / "api-ops", capability_host.ops,
        {"operation_id": identity, "kind": "package-inspect", "package": "openssl"},
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
