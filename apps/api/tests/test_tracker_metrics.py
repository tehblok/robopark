import threading
from unittest.mock import patch

from robopark_api.services import tracker_cache, tracker_metrics


def test_daily_flow_counts_creations_and_resolutions():
    arrived = tracker_metrics.build_arrived_today_query("ROBOPARK", "north")
    departed = tracker_metrics.build_done_today_query("ROBOPARK", "north")
    assert "Created: today()" in arrived
    assert "Resolution:" not in arrived
    assert "Resolved: today()" in departed
    assert "Updated:" not in departed


def test_build_backlog_query_no_donor_exclude_when_empty():
    q = tracker_metrics.build_backlog_query("ROBOPARK", "Alpha", "")
    assert "Queue: ROBOPARK" in q
    assert "Tags: Alpha" in q
    assert "Tags: !" not in q


def test_build_backlog_query_excludes_donor():
    q = tracker_metrics.build_backlog_query("ROBOPARK", "Alpha", "donor")
    assert 'Tags: !"donor"' in q
    assert "-Tags:" not in q


def test_collect_park_metrics_fixed_order():
    with patch(
        "robopark_api.services.tracker_metrics.count_issues",
        side_effect=[3, 1, 0, 2, 0, 1, 4, 5],
    ) as mocked:
        result = tracker_metrics.collect_park_metrics(token="t", queue="ROBOPARK", tag="Alpha")
    assert mocked.call_count == 8
    assert result == {
        "open_blockers": 3,
        "backlog": 1,
        "in_transit": 0,
        "queued": 2,
        "waiting_team": 0,
        "waiting_parts": 1,
        "arrived": 4,
        "done": 5,
    }


def test_metrics_cache_roundtrip():
    tracker_metrics.clear_metrics_cache()
    tracker_metrics.set_cached_now_report("k", {"totals": {"blocker": 1}}, ttl_sec=60)
    assert tracker_metrics.get_cached_now_report("k") == {"totals": {"blocker": 1}}


def test_metrics_cache_expired_returns_none():
    tracker_metrics.clear_metrics_cache()
    tracker_metrics.set_cached_now_report("k", {"totals": {"blocker": 1}}, ttl_sec=0)
    assert tracker_metrics.get_cached_now_report("k") is None


def test_overlapping_collect_park_metrics_same_park_runs_counts_once():
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_collect(**kwargs):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2.0)
        return {"open_blockers": 1, "arrived": 2, "done": 3, "queued": 0, "in_transit": 0}

    with patch(
        "robopark_api.services.tracker_metrics.collect_park_metrics",
        side_effect=fake_collect,
    ):
        results: list[dict] = []

        def worker() -> None:
            results.append(
                tracker_cache.collect_park_metrics(token="t", queue="ROBOPARK", tag="Alpha")
            )

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        assert started.wait(timeout=2.0)
        release.set()
        for t in threads:
            t.join(timeout=2.0)

    assert calls == 1
    assert results == [results[0]] * 4
    assert "user" not in str(results[0])


def test_collect_park_metrics_keys_by_park_not_user():
    calls: list[str] = []

    def fake_collect(*, token, queue, tag, priority="blocker", issue_type=None):
        calls.append(tag)
        return {"open_blockers": 1, "arrived": 0, "done": 0, "queued": 0, "in_transit": 0}

    with patch(
        "robopark_api.services.tracker_metrics.collect_park_metrics",
        side_effect=fake_collect,
    ):
        tracker_cache.collect_park_metrics(token="t1", queue="ROBOPARK", tag="Alpha")
        tracker_cache.collect_park_metrics(token="t2", queue="ROBOPARK", tag="Alpha")
        tracker_cache.collect_park_metrics(token="t1", queue="ROBOPARK", tag="Beta")

    assert calls == ["Alpha", "Beta"]
