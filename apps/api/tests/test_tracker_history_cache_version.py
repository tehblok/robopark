"""An observed Tracker update must invalidate an older changelog projection."""

from robopark_api.services import tracker_client


def test_history_cache_is_scoped_to_observed_issue_version(monkeypatch):
    tracker_client.clear_issue_status_history_cache()
    reads = []

    def fetch(**kw):
        reads.append(kw["issue"]["updated"])
        return [] if len(reads) == 1 else [{"id": "confirmed-queue"}]

    monkeypatch.setattr(tracker_client, "_fetch_issue_status_history", fetch)
    old = {"key": "RP-VERSION", "updated": "2026-09-30T06:00:00Z"}
    fresh = {"key": "RP-VERSION", "updated": "2026-09-30T06:00:20Z"}
    assert (
        tracker_client.get_issue_status_history(token="fixture-token", key=old["key"], issue=old)
        == []
    )
    assert tracker_client.get_issue_status_history(
        token="fixture-token", key=fresh["key"], issue=fresh
    ) == [{"id": "confirmed-queue"}]
    future, started = tracker_client.schedule_issue_status_history(
        token="fixture-token", key=fresh["key"], issue=fresh
    )
    assert not started
    assert future.result() == [{"id": "confirmed-queue"}]
    assert len(reads) == 2
    tracker_client.invalidate_issue_status_history(fresh["key"])
    tracker_client.get_issue_status_history(token="fixture-token", key=fresh["key"], issue=fresh)
    assert len(reads) == 3
    tracker_client.clear_issue_status_history_cache()
