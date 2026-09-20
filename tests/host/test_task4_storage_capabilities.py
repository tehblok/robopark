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

    cache = tmp_path / "cache"
    diagnostics = tmp_path / "diagnostics"
    confirmed = tmp_path / "confirmed"
    outside = tmp_path / "outside"
    for root in (cache, diagnostics, confirmed, outside):
        root.mkdir()
    (cache / "old.cache").write_bytes(b"c" * 10)
    (diagnostics / "old.log").write_bytes(b"d" * 10)
    (confirmed / "old.upload").write_bytes(b"u" * 10)
    protected = outside / "primary.db"
    protected.write_bytes(b"primary")
    (cache / "escape").symlink_to(protected)

    report = cleanup_storage_roots(
        {"cache": cache, "diagnostics": diagnostics, "confirmed_tracker": confirmed},
        StorageBudget(partition_bytes=100, free_bytes=0, minimum_free_bytes=25),
        dry_run=False,
        max_deletions=2,
        now=10_000,
    )

    assert [item["category"] for item in report["deleted"]] == ["cache", "diagnostics"]
    assert not (cache / "old.cache").exists()
    assert not (diagnostics / "old.log").exists()
    assert (confirmed / "old.upload").exists()
    assert protected.read_bytes() == b"primary"
    assert report["bounded"] is True


def test_storage_cleanup_dry_run_has_no_side_effects(tmp_path):
    from robopark_host.retention import StorageBudget, cleanup_storage_roots

    cache = tmp_path / "cache"
    cache.mkdir()
    victim = cache / "entry"
    victim.write_bytes(b"123")

    report = cleanup_storage_roots(
        {"cache": cache},
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
    cache = outside / "cache"
    cache.mkdir(parents=True)
    victim = cache / "entry"
    victim.write_bytes(b"safe")
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)

    report = cleanup_storage_roots(
        {"cache": alias / "cache"},
        StorageBudget(100, 0, minimum_free_bytes=3),
        dry_run=False,
    )

    assert report["blocked"] is True
    assert victim.read_bytes() == b"safe"


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
    assert healthy_orin.jpeg_backend == "nvjpeg"
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


def test_scheduled_storage_retention_uses_only_named_ephemeral_roots(
    host_paths, monkeypatch
):
    from robopark_host import retention

    cache = host_paths.var / "cache"
    cache.mkdir(parents=True)
    (cache / "old").write_bytes(b"cache")
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
