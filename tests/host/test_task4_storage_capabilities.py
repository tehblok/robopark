import hashlib
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest


def test_storage_budget_uses_larger_partition_floor():
    from robopark_host.retention import StorageBudget

    small = StorageBudget(partition_bytes=20 * 1024**3, free_bytes=7 * 1024**3)
    large = StorageBudget(partition_bytes=100 * 1024**3, free_bytes=14 * 1024**3)

    assert small.floor_bytes == 6 * 1024**3
    assert small.bytes_to_reclaim == 0
    assert large.floor_bytes == 15 * 1024**3
    assert large.bytes_to_reclaim == 1024**3


def test_bounded_cleanup_orders_known_categories_and_never_follows_symlinks(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    diagnostics = tmp_path / "diagnostics"
    logs = tmp_path / "logs"
    outside = tmp_path / "outside"
    for root in (diagnostics, logs, outside):
        root.mkdir()
    (diagnostics / "old.log").write_bytes(b"d" * 10)
    (logs / "old.log").write_bytes(b"u" * 10)
    protected = outside / "primary.db"
    protected.write_bytes(b"primary")
    (diagnostics / "escape").symlink_to(protected)

    report = cleanup_storage_roots(
        {"diagnostics": diagnostics, "logs": logs},
        StorageBudget(partition_bytes=100, free_bytes=0, minimum_free_bytes=25),
        dry_run=False,
        max_deletions=2,
        now=10_000,
    )

    assert [item["category"] for item in report["deleted"]] == ["diagnostics", "logs"]
    assert not (diagnostics / "old.log").exists()
    assert not (logs / "old.log").exists()
    assert protected.read_bytes() == b"primary"
    assert report["bounded"] is True


def test_storage_cleanup_dry_run_has_no_side_effects(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    victim = diagnostics / "entry"
    victim.write_bytes(b"123")

    report = cleanup_storage_roots(
        {"diagnostics": diagnostics},
        StorageBudget(partition_bytes=100, free_bytes=0, minimum_free_bytes=3),
        dry_run=True,
        max_deletions=1,
        now=10_000,
    )

    assert victim.exists()
    assert report["deleted"] == []
    assert report["planned"][0]["path"] == "entry"


def test_cleanup_execute_requires_the_exact_immutable_preview(tmp_path):
    from robopark_host.retention import (
        StorageBudget,
        execute_cleanup_plan,
        preview_cleanup_plan,
    )

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    victim = diagnostics / "entry"
    victim.write_bytes(b"123")
    roots = {"diagnostics": diagnostics}
    plan = preview_cleanup_plan(
        roots,
        StorageBudget(100, 0, minimum_free_bytes=3),
        now=10_000,
    )
    assert victim.exists()
    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        execute_cleanup_plan(
            roots,
            StorageBudget(100, 0, minimum_free_bytes=3),
            {**plan, "plan_id": "00000000-0000-4000-8000-000000000000"},
            now=10_000,
        )
    result = execute_cleanup_plan(
        roots,
        StorageBudget(100, 0, minimum_free_bytes=3),
        plan,
        now=10_000,
    )
    assert result["plan_id"] == plan["plan_id"]
    assert not victim.exists()


def _managed_cleanup_fixture(host_paths, *, guard_age=0):
    from robopark_host.state import atomic_write_json

    now = time.time()
    releases = {name: host_paths.releases / name for name in ("recovery-a", "obsolete-b", "previous-c", "current-d")}
    receipts = host_paths.state / "successful-releases"
    receipts.mkdir(parents=True, exist_ok=True)
    for index, (name, path) in enumerate(releases.items()):
        path.mkdir(parents=True)
        (path / "payload").write_bytes((name * 8).encode())
        receipt = receipts / f"{name}.json"
        receipt.write_text('{"successful":true}')
        os.utime(receipt, (index + 1, index + 1))
    host_paths.current.symlink_to(releases["current-d"])
    host_paths.previous.symlink_to(releases["previous-c"])
    host_paths.recovery.symlink_to(releases["recovery-a"])

    backup_root = host_paths.var / "backups"
    backup_root.mkdir(parents=True)
    guard_id = str(uuid4())
    guard = backup_root / f"backup-{guard_id}.rpb"
    guard.write_bytes(b"verified recovery backup")
    device_uuid = str(uuid4())
    mount = host_paths.root / "mnt/usb/robopark-backups"
    mount.mkdir(parents=True)
    usb_backup = mount / guard.name
    usb_backup.write_bytes(guard.read_bytes())
    usb_backup.chmod(0o600)
    device = host_paths.root / "dev/fake-usb"
    device.parent.mkdir(parents=True)
    device.touch()
    uuid_root = host_paths.root / "dev/disk/by-uuid"
    uuid_root.mkdir(parents=True)
    (uuid_root / device_uuid).symlink_to("../../fake-usb")
    removable = host_paths.root / "sys/class/block/fake-usb/removable"
    removable.parent.mkdir(parents=True)
    removable.write_text("1\n")
    mountinfo = host_paths.root / "proc/self/mountinfo"
    mountinfo.parent.mkdir(parents=True)
    mountinfo.write_text("1 0 8:1 / /mnt/usb rw - ext4 /dev/fake-usb rw\n")
    atomic_write_json(
        host_paths.state / "selected-usb.json",
        {"schema": 1, "device_uuid": device_uuid},
    )
    receipt = {
        "schema": 1,
        "backup_id": guard_id,
        "device_uuid": device_uuid,
        "verified": True,
        "sha256": hashlib.sha256(guard.read_bytes()).hexdigest(),
        "verified_at": now - guard_age,
        "recovery_required": True,
    }
    atomic_write_json(host_paths.state / "backup-receipts" / f"{guard_id}.json", receipt)
    old_id = str(uuid4())
    old = backup_root / f"backup-{old_id}.rpb"
    old.write_bytes(b"old unverified backup")
    os.utime(old, (1, 1))
    return now, guard, old, releases


@pytest.mark.parametrize("change", ["selection", "legacy", "usb-artifact"])
def test_cleanup_guard_rejects_stale_usb_binding(host_paths, change):
    from robopark_host.operational_state import read_object
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )
    from robopark_host.state import atomic_write_json

    now, guard, old, releases = _managed_cleanup_fixture(host_paths)
    budget = StorageBudget(10_000, 0, minimum_free_bytes=10_000)
    plan = preview_host_cleanup_plan(host_paths, ["releases"], budget, now=now, large_cleanup_bytes=1)
    assert plan["blocked"] is False
    assert plan["guard"]["device_uuid"] == read_object(host_paths.state / "selected-usb.json")["device_uuid"]
    if change == "selection":
        atomic_write_json(host_paths.state / "selected-usb.json", {"schema": 1, "device_uuid": str(uuid4())})
    elif change == "legacy":
        path = next((host_paths.state / "backup-receipts").iterdir())
        receipt = read_object(path)
        receipt.pop("device_uuid")
        atomic_write_json(path, receipt)
    else:
        (host_paths.root / "mnt/usb/robopark-backups" / guard.name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        execute_host_cleanup_plan(host_paths, ["releases"], budget, plan, now=now, large_cleanup_bytes=1)
    assert old.exists() and releases["obsolete-b"].exists()


def test_managed_cleanup_plans_actual_backup_and_release_candidates(host_paths):
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )

    now, guard, old, releases = _managed_cleanup_fixture(host_paths)
    plan = preview_host_cleanup_plan(
        host_paths,
        ["backups", "releases"],
        StorageBudget(10_000, 0, minimum_free_bytes=10_000),
        now=now,
        large_cleanup_bytes=1,
    )

    planned = {(item["category"], item["path"]) for item in plan["planned"]}
    assert ("backups", old.name) in planned
    assert ("releases", "obsolete-b") in planned
    assert all(guard.name != item["path"] for item in plan["planned"])
    assert not any(item["path"] in {"recovery-a", "previous-c", "current-d"} for item in plan["planned"])
    assert plan["blocked"] is False
    result = execute_host_cleanup_plan(
        host_paths,
        ["backups", "releases"],
        StorageBudget(10_000, 0, minimum_free_bytes=10_000),
        plan,
        now=now,
        large_cleanup_bytes=1,
    )
    assert result["plan_id"] == plan["plan_id"]
    assert not old.exists() and not releases["obsolete-b"].exists()
    assert guard.exists() and releases["recovery-a"].exists()


@pytest.mark.parametrize("guard", ["missing", "stale", "changed"])
def test_managed_cleanup_requires_fresh_unchanged_verified_backup_guard(
    host_paths, guard
):
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )

    now, verified, old, releases = _managed_cleanup_fixture(
        host_paths, guard_age=90_000 if guard == "stale" else 0
    )
    if guard == "missing":
        next((host_paths.state / "backup-receipts").iterdir()).unlink()
    budget = StorageBudget(10_000, 0, minimum_free_bytes=10_000)
    plan = preview_host_cleanup_plan(
        host_paths,
        ["backups", "releases"],
        budget,
        now=now,
        large_cleanup_bytes=1,
        backup_guard_max_age=86_400,
    )
    if guard in {"missing", "stale"}:
        assert plan["blocked"] is True
        assert old.exists() and releases["obsolete-b"].exists()
        return

    assert plan["blocked"] is False
    verified.write_bytes(b"changed after preview")
    with pytest.raises(ValueError, match="cleanup_plan_changed"):
        execute_host_cleanup_plan(
            host_paths,
            ["backups", "releases"],
            budget,
            plan,
            now=now,
            large_cleanup_bytes=1,
            backup_guard_max_age=86_400,
        )
    assert old.exists() and releases["obsolete-b"].exists()


@pytest.mark.parametrize("guard", ["fresh", "missing", "stale", "changed"])
def test_release_only_cleanup_uses_independent_verified_backup_guard(host_paths, guard):
    from robopark_host.retention import (
        StorageBudget,
        execute_host_cleanup_plan,
        preview_host_cleanup_plan,
    )

    now, verified, old, releases = _managed_cleanup_fixture(
        host_paths, guard_age=90_000 if guard == "stale" else 0
    )
    if guard == "missing":
        next((host_paths.state / "backup-receipts").iterdir()).unlink()
    if guard == "changed":
        verified.write_bytes(b"changed before preview")
    budget = StorageBudget(10_000, 0, minimum_free_bytes=10_000)
    plan = preview_host_cleanup_plan(
        host_paths,
        ["releases"],
        budget,
        now=now,
        large_cleanup_bytes=1,
        backup_guard_max_age=86_400,
    )

    if guard != "fresh":
        assert plan["blocked"] is True
        assert releases["obsolete-b"].exists()
        return

    assert plan["blocked"] is False
    assert plan["guard"]["backup_id"] in verified.name
    result = execute_host_cleanup_plan(
        host_paths,
        ["releases"],
        budget,
        plan,
        now=now,
        large_cleanup_bytes=1,
        backup_guard_max_age=86_400,
    )
    assert result["deleted_count"] == 1
    assert not releases["obsolete-b"].exists()
    assert verified.exists() and old.exists()


def test_storage_cleanup_rejects_a_root_below_a_symlinked_parent(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    outside = tmp_path / "outside"
    diagnostics = outside / "diagnostics"
    diagnostics.mkdir(parents=True)
    victim = diagnostics / "entry"
    victim.write_bytes(b"safe")
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)

    report = cleanup_storage_roots(
        {"diagnostics": alias / "diagnostics"},
        StorageBudget(100, 0, minimum_free_bytes=3),
        dry_run=False,
    )

    assert report["blocked"] is True
    assert victim.read_bytes() == b"safe"


def test_storage_cleanup_parent_replacement_cannot_delete_outside(
    tmp_path, monkeypatch
):
    import os

    from robopark_host import retention

    owned = tmp_path / "owned"
    outside = tmp_path / "outside"
    owned.mkdir()
    outside.mkdir()
    (owned / "victim").write_bytes(b"owned")
    protected = outside / "victim"
    protected.write_bytes(b"outside")
    moved = tmp_path / "moved"
    real_unlink = os.unlink
    replaced = False

    def replace_parent_then_unlink(name, *, dir_fd=None):
        nonlocal replaced
        if not replaced:
            owned.rename(moved)
            owned.symlink_to(outside, target_is_directory=True)
            replaced = True
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(retention.os, "unlink", replace_parent_then_unlink)
    report = retention.cleanup_storage_roots(
        {"diagnostics": owned},
        retention.StorageBudget(100, 0, minimum_free_bytes=1),
        dry_run=False,
    )

    assert report["deleted_count"] == 1
    assert protected.read_bytes() == b"outside"


def test_storage_cleanup_scan_and_report_are_bounded(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    diagnostics = tmp_path / "diagnostics"
    diagnostics.mkdir()
    for index in range(1000):
        (diagnostics / f"entry-{index:04d}").write_bytes(b"x")
    for index in range(1000):
        (diagnostics / f"link-{index:04d}").symlink_to(diagnostics / "entry-0000")

    report = cleanup_storage_roots(
        {"diagnostics": diagnostics},
        StorageBudget(10_000, 0, minimum_free_bytes=10_000),
        dry_run=True,
        max_deletions=128,
    )

    assert len(report["planned"]) == 128
    assert report["skipped_counts"] == {"not_owned_file": 1000}
    assert "skipped" not in report


def test_candidate_bound_does_not_hide_a_category_over_its_cap(tmp_path):
    import os

    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    diagnostics = tmp_path / "diagnostics"
    logs = tmp_path / "logs"
    diagnostics.mkdir()
    logs.mkdir()
    expired = diagnostics / "expired"
    expired.write_bytes(b"old")
    os.utime(expired, (1, 1))
    (diagnostics / "fresh").write_bytes(b"fresh")
    oversized_log = logs / "fresh.log"
    with oversized_log.open("wb") as stream:
        stream.truncate(300 * 1024**2)

    report = cleanup_storage_roots(
        {"diagnostics": diagnostics, "logs": logs},
        StorageBudget(100, 100, minimum_free_bytes=0),
        dry_run=True,
        max_deletions=1,
        now=8 * 86400,
    )

    assert report["planned"] == [
        {"category": "logs", "path": "fresh.log", "bytes": 300 * 1024**2}
    ]


def _fixture(root: Path, *, model: str, compatible: str, devices=(), plugins=()):
    (root / "proc/device-tree").mkdir(parents=True)
    (root / "proc/device-tree/model").write_text(model)
    (root / "proc/device-tree/compatible").write_text(compatible)
    for device in devices:
        path = root / device.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    for plugin in plugins:
        path = root / plugin.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_capability_fixtures_require_a_healthy_jpeg_probe(tmp_path):
    from robopark_host.capabilities import probe_host_capabilities

    cases = [
        ("vim4", "Khadas VIM4", "khadas,vim4", (), (), "vim4", False),
        (
            "new-vim4",
            "Khadas New VIM4",
            "khadas,new-vim4",
            ("/dev/aml-npu",),
            ("/usr/lib/libgstaml.so",),
            "new-vim4",
            True,
        ),
        (
            "orin",
            "NVIDIA Jetson AGX Orin",
            "nvidia,tegra234",
            ("/dev/nvhost-nvdec",),
            ("/usr/lib/libnvjpeg.so",),
            "orin",
            False,
        ),
        ("generic", "Generic ARM", "linux,dummy-virt", (), (), "generic-arm", False),
    ]
    for name, model, compatible, devices, plugins, profile, npu in cases:
        root = tmp_path / name
        _fixture(
            root, model=model, compatible=compatible, devices=devices, plugins=plugins
        )
        capability = probe_host_capabilities(
            root, jpeg_health_probe=lambda backend: False
        )
        assert capability.profile == profile
        assert capability.jpeg_backend == "software"
        assert capability.npu_available is npu

    healthy_orin = probe_host_capabilities(
        tmp_path / "orin", jpeg_health_probe=lambda backend: backend == "nvjpeg"
    )
    assert healthy_orin.jpeg_backend == "software"
    assert healthy_orin.hardware_jpeg is False
    assert healthy_orin.cuda_available is True


def test_doctor_names_pressure_category_and_last_cleanup(host_paths):
    from robopark_host.doctor import _storage_retention_check
    from robopark_host.state import atomic_write_json

    atomic_write_json(
        host_paths.state / "storage-retention.json",
        {
            "blocked": True,
            "pressure": True,
            "pressure_category": "diagnostics",
            "completed_at": 123,
        },
    )
    check = _storage_retention_check(host_paths)
    assert check.status == "failed"
    assert "diagnostics" in check.message
    assert "123" in check.message


def test_public_host_projection_is_sanitized(host_paths):
    import json

    from robopark_host.capabilities import HostCapabilities, write_capabilities

    public = host_paths.var / "api-ops/host-health.json"
    write_capabilities(
        host_paths.state / "capabilities.json",
        HostCapabilities(
            "orin", "secret serial model", "software", False, False, True, True
        ),
        public_path=public,
    )

    value = json.loads(public.read_text())
    assert value["capabilities"]["profile"] == "orin"
    assert "model" not in value["capabilities"]
    assert str(host_paths.root) not in public.read_text()


def test_scheduled_storage_retention_uses_only_named_ephemeral_roots(
    host_paths, monkeypatch
):
    from robopark_host import retention

    diagnostics = host_paths.var / "diagnostics"
    diagnostics.mkdir(parents=True)
    (diagnostics / "old").write_bytes(b"diagnostic")
    primary = host_paths.var / "inventory.db"
    primary.write_bytes(b"primary")
    monkeypatch.setattr(
        retention.StorageBudget,
        "for_path",
        classmethod(lambda cls, path: cls(100, 0, minimum_free_bytes=5)),
    )

    report = retention.retain_storage(host_paths)

    assert report["deleted_count"] == 1
    assert primary.read_bytes() == b"primary"
    assert (host_paths.state / "storage-retention.json").is_file()
