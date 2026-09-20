from pathlib import Path


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


def test_storage_cleanup_parent_replacement_cannot_delete_outside(tmp_path, monkeypatch):
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
        HostCapabilities("orin", "secret serial model", "software", False, False, True, True),
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
