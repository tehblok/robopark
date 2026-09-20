def test_process_observations_include_leak_and_cache_pool_signals(tmp_path, monkeypatch):
    from robopark_api.services import operational_health

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
