def test_process_observations_include_leak_and_cache_pool_signals(tmp_path, monkeypatch):
    from robopark_api.services import operational_health

    monkeypatch.setattr(
        operational_health,
        "_rss_samples",
        type(operational_health._rss_samples)(maxlen=operational_health._rss_samples.maxlen),
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "entry").write_bytes(b"1234")
    cleanup = tmp_path / "cleanup.json"
    cleanup.write_text('{"completed_at":123,"blocked":false}')
    monkeypatch.setattr(
        operational_health, "snapshot_all", lambda: {"tracker": {"bytes": 17, "entries": 2}}
    )
    monkeypatch.setattr(operational_health.engine.pool, "checkedout", lambda: 3, raising=False)

    observed = operational_health.process_observations(
        category_roots={"cache": cache}, cleanup_state=cleanup
    )

    assert observed["rss_bytes"] is None or observed["rss_bytes"] > 0
    assert observed["rss_trend_bytes"] == 0
    assert observed["open_fds"] is None or observed["open_fds"] >= 0
    assert observed["tasks"] >= 1
    assert observed["threads"] >= 1
    assert observed["cache_bytes"] == 17
    assert observed["db_pool_checked_out"] == 3
    assert observed["directory_bytes"] == {"cache": 4}
    assert observed["last_cleanup"]["completed_at"] == 123


def test_sustained_pressure_evicts_cache_once_and_reports_failure():
    from robopark_api.services.storage_retention import MemoryPressureController

    calls = []
    controller = MemoryPressureController(required_samples=3, evict=lambda: calls.append("cache"))
    assert controller.observe(0.91) == {"sustained": False, "evicted": False, "failed": False}
    assert controller.observe(0.92)["sustained"] is False
    assert controller.observe(0.93) == {"sustained": True, "evicted": True, "failed": False}
    assert controller.observe(0.94) == {"sustained": True, "evicted": False, "failed": True}
    assert calls == ["cache"]


def test_pressure_controller_surfaces_cache_eviction_failure():
    from robopark_api.services.storage_retention import MemoryPressureController

    def fail():
        raise RuntimeError("cache backend unavailable")

    controller = MemoryPressureController(required_samples=1, evict=fail)
    assert controller.observe(0.99) == {
        "sustained": True,
        "evicted": False,
        "failed": True,
    }


def test_application_pressure_handler_clears_caches_before_failure(monkeypatch):
    from robopark_api.services import cache_cleanup

    calls = []
    monkeypatch.setattr(cache_cleanup.tracker_cache, "clear_all", lambda: calls.append("tracker"))
    monkeypatch.setattr(
        cache_cleanup.emergency_cache, "clear_cache", lambda: calls.append("emergency")
    )
    cache_cleanup.reset_memory_pressure_controller()

    assert cache_cleanup.observe_memory_pressure(0.95)["failed"] is False
    assert cache_cleanup.observe_memory_pressure(0.95)["failed"] is False
    assert cache_cleanup.observe_memory_pressure(0.95)["evicted"] is True
    assert calls == ["tracker", "emergency"]
    assert cache_cleanup.observe_memory_pressure(0.95)["failed"] is True


def test_memory_pressure_sample_reads_cgroup_and_exposes_last_result(tmp_path):
    from robopark_api.services import cache_cleanup

    (tmp_path / "memory.max").write_text("100")
    (tmp_path / "memory.current").write_text("95")
    cache_cleanup.reset_memory_pressure_controller(required_samples=1)

    result = cache_cleanup.sample_memory_pressure(tmp_path)

    assert result == {"sustained": True, "evicted": True, "failed": False}
    assert cache_cleanup.memory_pressure_status() == result


def test_api_retention_deletes_only_confirmed_tracker_duplicates(tmp_path):
    from robopark_api.services.storage_retention import StorageBudget, cleanup_storage

    confirmed = tmp_path / "confirmed"
    unconfirmed = tmp_path / "unconfirmed"
    actions = tmp_path / "actions"
    for root in (confirmed, unconfirmed, actions):
        root.mkdir()
        (root / "blob").write_bytes(b"123")

    report = cleanup_storage(
        roots={"confirmed_tracker": confirmed},
        budget=StorageBudget(100, 0, minimum_free_bytes=3),
        dry_run=False,
        max_deletions=10,
    )

    assert report["deleted_count"] == 1
    assert not (confirmed / "blob").exists()
    assert (unconfirmed / "blob").exists()
    assert (actions / "blob").exists()


def test_api_retention_parent_replacement_and_report_are_bounded(tmp_path, monkeypatch):
    import os

    from robopark_api.services import storage_retention

    confirmed = tmp_path / "confirmed"
    outside = tmp_path / "outside"
    confirmed.mkdir()
    outside.mkdir()
    for index in range(1000):
        (confirmed / f"blob-{index:04d}").write_bytes(b"x")
        (confirmed / f"link-{index:04d}").symlink_to(outside / "protected")
    protected = outside / "protected"
    protected.write_bytes(b"outside")
    moved = tmp_path / "moved"
    original = os.unlink
    replaced = False

    def replace_then_unlink(name, *, dir_fd=None):
        nonlocal replaced
        if not replaced:
            confirmed.rename(moved)
            confirmed.symlink_to(outside, target_is_directory=True)
            replaced = True
        return original(name, dir_fd=dir_fd)

    monkeypatch.setattr(storage_retention.os, "unlink", replace_then_unlink)
    report = storage_retention.cleanup_storage(
        roots={"confirmed_tracker": confirmed},
        budget=storage_retention.StorageBudget(10_000, 0, minimum_free_bytes=10_000),
        dry_run=False,
        max_deletions=128,
    )

    assert report["deleted_count"] == 128
    assert len(report["planned"]) == 128
    assert report["skipped_counts"] == {"not_owned_file": 1000}
    assert protected.read_bytes() == b"outside"


def test_api_retention_reports_partial_when_protected_scan_reaches_budget(tmp_path, monkeypatch):
    from robopark_api.services import storage_retention

    class ProtectedEntries:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def __iter__(self):
            for index in range(1_000_000):
                yield type(
                    "Entry",
                    (),
                    {
                        "name": f"protected-{index}",
                        "stat": lambda self, *, follow_symlinks: type(
                            "Info", (), {"st_mode": 0, "st_nlink": 1}
                        )(),
                    },
                )()

    root = tmp_path / "confirmed"
    root.mkdir()
    monkeypatch.setattr(storage_retention.os, "scandir", lambda descriptor: ProtectedEntries())

    report = storage_retention.cleanup_storage(
        roots={"confirmed_tracker": root},
        budget=storage_retention.StorageBudget(100, 0, minimum_free_bytes=1),
        dry_run=False,
        max_deletions=1,
        max_scanned_entries=4,
    )

    assert report["scanned_count"] == 4
    assert report["partial"] is True
    assert report["stop_reason"] == "scan_budget"
    assert report["eligible_missing"] == {}


def test_cleanup_retry_is_bounded_and_rate_limited():
    from robopark_api.services.storage_retention import CleanupRetry

    retry = CleanupRetry(minimum=30, maximum=120)
    assert [retry.failed() for _ in range(5)] == [30, 60, 120, 120, 120]
    assert retry.should_log(0) is True
    assert retry.should_log(119) is False
    assert retry.should_log(120) is True
    retry.succeeded()
    assert retry.failed() == 30


def test_host_snapshot_reads_actual_data_mount_and_only_public_projection(tmp_path, monkeypatch):
    import json
    import time
    from datetime import UTC, datetime

    from robopark_api.services import (
        live_merge,
        operational_health,
        report_attachments,
        task_timeline,
    )

    data = tmp_path / "data"
    ops = tmp_path / "ops"
    private = ops / "state"
    for path in (data, ops, private):
        path.mkdir()
    public = ops / "host-health.json"
    public_payload = {
        "backup_attempt": {
            "status": "failed",
            "completed_at": datetime.now(UTC).isoformat(),
        },
        "capabilities": {
            "profile": "orin",
            "jpeg_backend": "software",
            "checked_at": time.time(),
            "hardware_jpeg": False,
            "cuda_available": True,
        },
        "storage": {
            "completed_at": time.time(),
            "blocked": False,
            "pressure": False,
            "inventory_sampled_at": time.time(),
            "category_bytes": {
                "logs": 100,
                "diagnostics": 50,
                "backups": 75,
                "scheduled_backups": 175,
                "releases": 125,
                "ota_uploads": 1024,
                "docker_images": 2000,
                "buildkit_cache": None,
                "robopark_buildkit_reported": 8192,
                "robopark_buildkit_private_reclaimable": 4096,
                "postgresql_data": None,
            },
        },
    }
    public.write_text(json.dumps(public_payload))
    (ops / "api-storage-retention.json").write_text(
        '{"completed_at":124,"pressure":false,"owners":{"cache_tmp":'
        '{"deleted_count":2,"batches":1}}}'
    )
    (private / "capabilities.json").write_text(
        '{"profile":"private-leak","jpeg_backend":"nvjpeg","hardware_jpeg":true}'
    )
    for name in ("live", "reports", "uploads"):
        (data / name).mkdir()
    (data / "live" / "payload").write_bytes(b"data")
    monkeypatch.setattr(live_merge, "default_live_merge_root", lambda: data / "live")
    monkeypatch.setattr(report_attachments, "attachments_root", lambda: data / "reports")
    monkeypatch.setattr(task_timeline, "staged_attachments_root", lambda: data / "uploads")
    operational_health._snapshot_cache.clear()

    result = operational_health.cached_host_snapshot(data, ops, public)

    assert result["disk"]["total_bytes"] > 0
    assert result["capabilities"]["profile"] == "orin"
    assert result["capabilities"]["jpeg_backend"] == "software"
    assert result["backup"]["last_attempt_failed"] is True
    assert result["storage"]["last_cleanup_at"] == public_payload["storage"]["completed_at"]
    assert result["storage"]["api_last_cleanup_at"] == 124
    assert result["storage"]["api_cleanup_owners"]["cache_tmp"]["deleted_count"] == 2
    assert result["storage"]["cleanup_busy"] is False
    assert set(result["process"]["directory_bytes"]) == {
        "live_merge",
        "report_attachments",
        "tracker_uploads",
    }
    assert result["storage"]["category_bytes"] == {
        "live_merge": 4,
        "report_attachments": 0,
        "tracker_uploads": 0,
        "logs": 100,
        "diagnostics": 50,
        "backups": 75,
        "scheduled_backups": 175,
        "releases": 125,
        "ota_uploads": 1024,
        "docker_images": 2000,
        "robopark_buildkit_reported": 8192,
        "robopark_buildkit_private_reclaimable": 4096,
    }
    assert result["storage"]["inventory_stale"] is False
    public_payload["storage"]["inventory_sampled_at"] = time.time() - 3600
    public.write_text(json.dumps(public_payload))
    operational_health._snapshot_cache.clear()
    stale = operational_health.cached_host_snapshot(data, ops, public)
    assert "ota_uploads" not in stale["storage"]["category_bytes"]
    assert "docker_images" not in stale["storage"]["category_bytes"]
    assert "robopark_buildkit_reported" not in stale["storage"]["category_bytes"]
    assert "backups" not in stale["storage"]["category_bytes"]
    assert "scheduled_backups" not in stale["storage"]["category_bytes"]
    assert "releases" not in stale["storage"]["category_bytes"]
    assert stale["storage"]["inventory_stale"] is True
    public_payload["storage"]["completed_at"] = time.time() - 3600
    public.write_text(json.dumps(public_payload))
    operational_health._snapshot_cache.clear()
    stale_cleanup = operational_health.cached_host_snapshot(data, ops, public)
    assert "logs" not in stale_cleanup["storage"]["category_bytes"]
    assert "diagnostics" not in stale_cleanup["storage"]["category_bytes"]
    assert stale_cleanup["storage"]["cleanup_stale"] is True
    public_payload["storage"]["completed_at"] = time.time()
    public_payload["storage"]["inventory_failed"] = True
    public.write_text(json.dumps(public_payload))
    operational_health._snapshot_cache.clear()
    failed = operational_health.cached_host_snapshot(data, ops, public)
    assert failed["storage"]["inventory_failed"] is True
    from types import SimpleNamespace

    public_payload["storage"].update(pressure=True, blocked=False)
    public.write_text(json.dumps(public_payload))
    monkeypatch.setattr(
        operational_health.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=10 * 1024**3, free=2 * 1024**3),
    )
    operational_health._snapshot_cache.clear()
    pressure = operational_health.cached_host_snapshot(data, ops, public)
    assert pressure["storage"]["space_pressure"] is True
    assert pressure["storage"]["cleanup_failed"] is False
    public_payload["storage"]["blocked"] = True
    public.write_text(json.dumps(public_payload))
    operational_health._snapshot_cache.clear()
    blocked = operational_health.cached_host_snapshot(data, ops, public)
    assert blocked["storage"]["cleanup_failed"] is True
    public_payload["storage"].update(busy=True, pressure=False)
    public.write_text(json.dumps(public_payload))
    operational_health._snapshot_cache.clear()
    busy = operational_health.cached_host_snapshot(data, ops, public)
    assert busy["storage"]["cleanup_busy"] is True
    assert busy["storage"]["cleanup_failed"] is False
    public_payload["storage"].update(busy="true", pressure=False)
    public.write_text(json.dumps(public_payload))
    operational_health._snapshot_cache.clear()
    malformed_busy = operational_health.cached_host_snapshot(data, ops, public)
    assert malformed_busy["storage"]["cleanup_busy"] is False
    assert malformed_busy["storage"]["cleanup_failed"] is True
    public_payload["storage"].update(busy=True, pressure=True)
    public.write_text(json.dumps(public_payload))
    operational_health._snapshot_cache.clear()
    pressured_busy = operational_health.cached_host_snapshot(data, ops, public)
    assert pressured_busy["storage"]["cleanup_busy"] is True
    assert pressured_busy["storage"]["cleanup_failed"] is True
    public_payload["storage"]["blocked"] = False
    public_payload["storage"].pop("busy")
    public_payload["storage"]["pressure"] = False
    public.write_text(json.dumps(public_payload))
    monkeypatch.setattr(
        operational_health.shutil,
        "disk_usage",
        lambda _: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    operational_health._snapshot_cache.clear()
    missing_disk = operational_health.cached_host_snapshot(data, ops, public)
    assert missing_disk["storage"]["space_pressure"] is None
    assert missing_disk["storage"]["bytes_to_reclaim"] is None
    public_payload["storage"].pop("completed_at")
    public.write_text(json.dumps(public_payload))
    operational_health._snapshot_cache.clear()
    no_cleanup_result = operational_health.cached_host_snapshot(data, ops, public)
    assert no_cleanup_result["storage"]["cleanup_failed"] is None


def test_production_storage_owner_env_resolves_under_measured_data_mount(monkeypatch):
    from pathlib import Path

    from robopark_api.config import reset_settings_cache
    from robopark_api.services.live_merge import default_live_merge_root
    from robopark_api.services.task_timeline import staged_attachments_root

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://robopark@db/robopark")
    monkeypatch.setenv("HOST_DATA_PATH", "/data")
    monkeypatch.setenv("LIVE_MERGE_DIR", "/data/live-merge")
    monkeypatch.setenv("STAGED_ATTACHMENTS_DIR", "/data/task-attachments")
    reset_settings_cache()

    assert default_live_merge_root() == Path("/data/live-merge")
    assert staged_attachments_root() == Path("/data/task-attachments")
