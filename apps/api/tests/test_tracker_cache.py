"""Integration tests for the tracker_cache facade: wraps tracker_client with TTL + single-flight."""

import threading
import time
from types import SimpleNamespace

from robopark_api.services import response_cache, tracker_cache, tracker_client
from robopark_api.services.live_merge import LiveMergeStore
from robopark_api.services.response_cache import ResponseCache


def test_issue_cache_keeps_sdk_resource_without_json_serialization(monkeypatch, tmp_path):
    """The queue and detail retain SDK resources for SLA history, not JSON blobs."""
    store = LiveMergeStore(tmp_path)
    monkeypatch.setattr(response_cache, "get_live_merge_store", lambda: store)
    tracker_cache.clear_all()
    resource = object()
    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [{"key": "SD-RESOURCE", "_tracker_resource": resource}],
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {"key": "SD-RESOURCE", "_tracker_resource": resource},
    )

    assert (
        tracker_cache.search_issues(token="t", query="resource-test")[0]["_tracker_resource"]
        is resource
    )
    assert tracker_cache.get_issue(token="t", key="SD-RESOURCE")["_tracker_resource"] is resource


def test_issue_cache_shares_sanitized_payload_without_dropping_leader_resource(tmp_path):
    store = LiveMergeStore(tmp_path)
    leader = tracker_cache._TrackerCache(
        60,
        name="tracker.issue.test",
        shared=store,
        shared_payload=tracker_cache._shared_issue_payload,
    )
    follower = tracker_cache._TrackerCache(
        60,
        name="tracker.issue.test",
        shared=store,
        shared_payload=tracker_cache._shared_issue_payload,
    )
    resource = object()

    leader_value = leader.get_or_load(
        "SD-RESOURCE",
        lambda: {"key": "SD-RESOURCE", "_tracker_resource": resource},
    )
    follower_value = follower.get_or_load(
        "SD-RESOURCE",
        lambda: (_ for _ in ()).throw(AssertionError("shared cache missed")),
    )

    assert leader_value["_tracker_resource"] is resource
    assert follower_value == {"key": "SD-RESOURCE"}


def test_shared_issue_keeps_public_sla_equal_without_sdk_resource(monkeypatch, tmp_path):
    history = [
        {
            "updatedAt": "2026-09-19T07:00:00Z",
            "fields": [
                {
                    "field": {"id": "status"},
                    "to": {"key": "queued", "display": "В очереди"},
                }
            ],
        }
    ]
    resource = SimpleNamespace(changelog=SimpleNamespace(get_all=lambda: history))
    monkeypatch.setattr(
        tracker_client,
        "_client",
        lambda _token: SimpleNamespace(issues={"SD-RESOURCE": resource}),
    )
    monkeypatch.setattr(tracker_client, "_run_tracked", lambda operation, **_kwargs: operation())
    store = LiveMergeStore(tmp_path)
    leader = tracker_cache._TrackerCache(
        60,
        name="tracker.issue.sla",
        shared=store,
        shared_payload=tracker_cache._shared_issue_payload,
    )
    follower = tracker_cache._TrackerCache(
        60,
        name="tracker.issue.sla",
        shared=store,
        shared_payload=tracker_cache._shared_issue_payload,
    )
    leader_issue = leader.get_or_load(
        "SD-RESOURCE",
        lambda: {"key": "SD-RESOURCE", "_tracker_resource": resource},
    )
    follower_issue = follower.get_or_load(
        "SD-RESOURCE",
        lambda: (_ for _ in ()).throw(AssertionError("shared cache missed")),
    )

    leader_sla = tracker_client.repair_sla_fields(
        leader_issue,
        status_history=tracker_client.get_issue_status_history(
            token="t", key="SD-RESOURCE", issue=leader_issue
        ),
    )
    follower_sla = tracker_client.repair_sla_fields(
        follower_issue,
        status_history=tracker_client.get_issue_status_history(
            token="t", key="SD-RESOURCE", issue=follower_issue
        ),
    )

    assert follower_sla == leader_sla
    assert follower_sla["queued_at"] == "2026-09-19T07:00:00Z"
    assert tracker_client._load_work_status_history(
        token="t", key="SD-RESOURCE", issue=follower_issue
    ) == [
        {
            "id": "",
            "updatedAt": "2026-09-19T07:00:00Z",
            "fields": [
                {
                    "field": {"id": "status"},
                    "to": {"key": "queued", "display": "В очереди"},
                    "from": {"key": "", "display": ""},
                }
            ],
        }
    ]


def test_status_history_projection_is_shared_across_workers(monkeypatch, tmp_path):
    history = [
        SimpleNamespace(
            updatedAt="2026-09-19T07:00:00Z",
            fields=[
                SimpleNamespace(
                    field=SimpleNamespace(id="status"),
                    to=SimpleNamespace(key="queued", display="В очереди"),
                )
            ],
        )
    ]
    resource = SimpleNamespace(changelog=SimpleNamespace(get_all=lambda: history))
    store = LiveMergeStore(tmp_path)
    monkeypatch.setattr(
        tracker_client,
        "_issue_status_history_cache",
        ResponseCache(60, name="tracker.issue_history.test", shared=store),
        raising=False,
    )
    leader = tracker_client.get_issue_status_history(
        token="t",
        key="SD-RESOURCE",
        issue={"key": "SD-RESOURCE", "_tracker_resource": resource},
    )
    monkeypatch.setattr(
        tracker_client,
        "_issue_status_history_cache",
        ResponseCache(60, name="tracker.issue_history.test", shared=store),
        raising=False,
    )
    monkeypatch.setattr(
        tracker_client,
        "_client",
        lambda _token: (_ for _ in ()).throw(AssertionError("shared history missed")),
    )

    follower = tracker_client.get_issue_status_history(
        token="t", key="SD-RESOURCE", issue={"key": "SD-RESOURCE"}
    )

    assert follower == leader
    assert follower == [
        {
            "id": "",
            "updatedAt": "2026-09-19T07:00:00Z",
            "fields": [
                {
                    "field": {"id": "status"},
                    "to": {"key": "queued", "display": "В очереди"},
                    "from": {"key": "", "display": ""},
                }
            ],
        }
    ]


def test_work_history_observes_shared_invalidation_from_another_worker(monkeypatch, tmp_path):
    store = LiveMergeStore(tmp_path)
    local = ResponseCache(60, name="tracker.issue_history.work", shared=store)
    peer = ResponseCache(60, name="tracker.issue_history.work", shared=store)
    monkeypatch.setattr(tracker_client, "_issue_status_history_cache", local)
    monkeypatch.setattr(
        tracker_client,
        "_fetch_issue_status_history",
        lambda **_kwargs: [{"updatedAt": "old", "fields": []}],
    )
    tracker_client.clear_issue_status_history_cache()

    first, started = tracker_client.schedule_issue_status_history(
        token="token", key="SD-1", issue={"key": "SD-1"}
    )
    assert started is True
    assert first is not None and first.result(timeout=2)[0]["updatedAt"] == "old"
    deadline = time.monotonic() + 2
    while tracker_client._work_history_flights and time.monotonic() < deadline:
        time.sleep(0.01)

    peer.invalidate("SD-1")
    peer.get_or_load("SD-1", lambda: [{"updatedAt": "fresh", "fields": []}])

    second, started = tracker_client.schedule_issue_status_history(
        token="token", key="SD-1", issue={"key": "SD-1"}, allow_start=False
    )

    assert started is False
    assert second is not None and second.result(timeout=2)[0]["updatedAt"] == "fresh"


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
    tracker_cache.invalidate_issue("SD-1", membership_changed=False)
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


def test_invalidate_issue_keeps_unrelated_list_projections(monkeypatch):
    calls: dict[str, int] = {"one": 0, "two": 0}

    def search(*, query, **_kwargs):
        calls[query] += 1
        return [{"key": "SD-1" if query == "one" else "SD-2", "n": calls[query]}]

    monkeypatch.setattr(tracker_client, "search_issues", search)
    assert tracker_cache.search_issues(token="t", query="one")[0]["n"] == 1
    assert tracker_cache.search_issues(token="t", query="two")[0]["n"] == 1

    tracker_cache.invalidate_issue("SD-1", membership_changed=False)

    assert tracker_cache.search_issues(token="t", query="one")[0]["n"] == 2
    assert tracker_cache.search_issues(token="t", query="two")[0]["n"] == 1


def test_membership_change_retires_blocked_projection_and_refetches(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def search(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert release.wait(timeout=1)
            return []
        return [{"key": "SD-1", "status_key": "open"}]

    monkeypatch.setattr(tracker_client, "search_issues", search)
    thread = threading.Thread(
        target=lambda: tracker_cache.search_issues(token="t", query="Status: open")
    )
    thread.start()
    assert started.wait(timeout=1)
    tracker_cache.invalidate_issue("SD-1", membership_changed=True)
    release.set()
    thread.join(timeout=1)

    assert tracker_cache.search_issues(token="t", query="Status: open") == [
        {"key": "SD-1", "status_key": "open"}
    ]
    assert calls == 2


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
