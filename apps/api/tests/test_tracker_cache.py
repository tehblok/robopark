"""Integration tests for the tracker_cache facade: wraps tracker_client with TTL + single-flight."""

from robopark_api.services import tracker_cache, tracker_client


def test_search_issues_shares_upstream_call(monkeypatch):
    calls = 0

    def fake_search(*, token, query, filter_open=True, order=None):
        nonlocal calls
        calls += 1
        return [{"key": "SD-1", "summary": query}]

    monkeypatch.setattr(tracker_client, "search_issues", fake_search)

    a = tracker_cache.search_issues(token="t", query="Q1")
    b = tracker_cache.search_issues(token="t", query="Q1")
    c = tracker_cache.search_issues(token="t", query="Q2")

    assert a == b == [{"key": "SD-1", "summary": "Q1"}]
    assert c == [{"key": "SD-1", "summary": "Q2"}]
    assert calls == 2


def test_get_issue_and_invalidate(monkeypatch):
    calls = 0

    def fake_get(*, token, key):
        nonlocal calls
        calls += 1
        return {"key": key, "n": calls}

    monkeypatch.setattr(tracker_client, "get_issue", fake_get)

    first = tracker_cache.get_issue(token="t", key="SD-1")
    _cached = tracker_cache.get_issue(token="t", key="SD-1")
    tracker_cache.invalidate_issue("SD-1")
    refreshed = tracker_cache.get_issue(token="t", key="SD-1")

    assert first == {"key": "SD-1", "n": 1}
    assert _cached == first
    assert refreshed == {"key": "SD-1", "n": 2}
    assert calls == 2


def test_invalidate_issue_clears_related_list_caches(monkeypatch):
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kw: [{"key": "SD-9"}])
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **_kw: [{"key": "SD-9"}])
    monkeypatch.setattr(
        tracker_client,
        "search_robot_tickets",
        lambda **_kw: [{"key": "SD-9"}],
    )

    tracker_cache.search_issues(token="t", query="q")
    tracker_cache.fetch_park_blockers(token="t", queue="Q", park_tag="P")
    tracker_cache.search_robot_tickets(token="t", queue="Q", query="42")

    tracker_cache.invalidate_issue("SD-9")

    # New loaders (updated status) should be picked up because the list caches were dropped.
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kw: [{"key": "SD-9-v2"}])
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **_kw: [{"key": "SD-9-v2"}])
    monkeypatch.setattr(
        tracker_client,
        "search_robot_tickets",
        lambda **_kw: [{"key": "SD-9-v2"}],
    )

    assert tracker_cache.search_issues(token="t", query="q") == [{"key": "SD-9-v2"}]
    assert tracker_cache.fetch_park_blockers(token="t", queue="Q", park_tag="P") == [
        {"key": "SD-9-v2"}
    ]
    assert tracker_cache.search_robot_tickets(token="t", queue="Q", query="42") == [
        {"key": "SD-9-v2"}
    ]


def test_clear_all_resets_every_cache(monkeypatch):
    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kw: {"key": "SD-1"})
    tracker_cache.get_issue(token="t", key="SD-1")

    tracker_cache.clear_all()

    calls = 0

    def counter(*, token, key):
        nonlocal calls
        calls += 1
        return {"key": key, "n": calls}

    monkeypatch.setattr(tracker_client, "get_issue", counter)
    assert tracker_cache.get_issue(token="t", key="SD-1") == {"key": "SD-1", "n": 1}


def test_count_issues_merges_by_query_not_token(monkeypatch):
    calls = 0

    def fake_count(*, token, query):
        nonlocal calls
        calls += 1
        return 9

    monkeypatch.setattr(tracker_client, "count_issues", fake_count)
    assert tracker_cache.count_issues(token="a", query="Queue: ROBOPARK") == 9
    assert tracker_cache.count_issues(token="b", query="Queue: ROBOPARK") == 9
    assert tracker_cache.count_issues(token="a", query="Queue: OTHER") == 9
    assert calls == 2
