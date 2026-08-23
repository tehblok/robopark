from unittest.mock import patch

from robopark_api.services import tracker_metrics


def test_build_backlog_query_no_donor_exclude_when_empty():
    q = tracker_metrics.build_backlog_query("ROBOPARK", "Alpha", "")
    assert "Queue: ROBOPARK" in q
    assert 'Tags: "Alpha"' in q
    assert 'Tags: !' not in q


def test_collect_park_metrics_fixed_order():
    with patch(
        "robopark_api.services.tracker_metrics.count_issues",
        side_effect=[3, 1, 0, 2, 0, 1, 4, 5],
    ) as mocked:
        result = tracker_metrics.collect_park_metrics(
            token="t", queue="ROBOPARK", tag="Alpha"
        )
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
