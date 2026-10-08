import threading
import time

import pytest

from robopark_api.services import bot_tracker_gateway, tracker_cache, tracker_client


def _raw_issue(key: str = "ROBOPARK-1") -> dict:
    return {
        "key": key,
        "summary": "[a447] repair",
        "description": "Broken wheel",
        "status": {"key": "open", "display": "Open"},
        "type": {"key": "repair", "display": "Repair"},
        "tags": ["Alpha"],
        "resolution": {"key": "", "display": ""},
        "priority": {"key": "blocker", "display": "Blocker"},
        "rover": "a447",
        "createdAt": "2026-10-01T10:00:00Z",
        "updatedAt": "2026-10-02T10:00:00Z",
        "resolvedAt": "",
    }


def _registry_issue(**overrides) -> dict:
    issue = {
        "key": "ROBOPARK-1",
        "summary": "[a447] repair",
        "description": "Broken wheel",
        "status": "Open",
        "status_key": "open",
        "type": "Repair",
        "type_key": "repair",
        "tags": ["Alpha"],
        "resolution": "",
        "resolution_key": "",
        "priority": "Blocker",
        "priority_key": "blocker",
        "rover": "a447",
        "status_start_time": "2026-10-01T11:00:00Z",
        "home_port": "Alpha garage",
        "created": "2026-10-01T10:00:00Z",
        "updated": "2026-10-02T10:00:00Z",
        "resolved": "",
    }
    issue.update(overrides)
    return issue


def _search(**overrides):
    kwargs = {
        "token": "token-a",
        "query": 'Queue: ROBOPARK Tags: "Alpha"',
        "order": ["-created"],
        "per_page": 50,
        "max_pages": 10,
        "allowed_queues": ("ROBOPARK",),
    }
    kwargs.update(overrides)
    return tracker_cache.search_native_robot_issues(**kwargs)


def test_native_robot_search_coalesces_and_returns_independent_copies(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def gateway_search(**_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2)
        return [_raw_issue()]

    monkeypatch.setattr(bot_tracker_gateway, "search", gateway_search)
    results: list[list[dict]] = []
    threads = [threading.Thread(target=lambda: results.append(_search())) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=1)
    release.set()
    for thread in threads:
        thread.join(timeout=2)

    assert calls == 1
    assert len(results) == 2
    results[0][0]["tags"].append("poison")
    results[0][0]["status"]["key"] = "closed"
    assert results[1] == [_raw_issue()]
    assert _search() == [_raw_issue()]


def test_native_robot_search_key_isolates_every_request_boundary(monkeypatch):
    calls = 0

    def gateway_search(**kwargs):
        nonlocal calls
        calls += 1
        return [_raw_issue(f"{kwargs['allowed_queues'][0]}-{calls}")]

    monkeypatch.setattr(bot_tracker_gateway, "search", gateway_search)

    _search()
    _search(query="Queue: ROBOPARK Status: open")
    _search(token="token-b")
    _search(order=["+created"])
    _search(per_page=20)
    _search(max_pages=2)
    _search(
        query="Queue: ROBOMAINT",
        allowed_queues=("ROBOMAINT",),
    )
    _search()

    assert calls == 7


def test_native_robot_search_expires_and_never_serves_stale_on_error(monkeypatch):
    calls = 0

    def gateway_search(**_kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise tracker_client.TrackerError("upstream failed")
        return [_raw_issue()]

    monkeypatch.setattr(bot_tracker_gateway, "search", gateway_search)
    monkeypatch.setattr(tracker_cache._native_robot_cache, "_ttl", 0.01)

    assert _search() == [_raw_issue()]
    time.sleep(0.02)
    with pytest.raises(tracker_client.TrackerError, match="upstream failed"):
        _search()

    assert calls == 2


def test_native_robot_search_invalidation_rejects_blocked_loader(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def gateway_search(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            assert release.wait(timeout=2)
            return [_raw_issue("ROBOPARK-old")]
        return [_raw_issue("ROBOPARK-fresh")]

    monkeypatch.setattr(bot_tracker_gateway, "search", gateway_search)
    thread = threading.Thread(target=_search)
    thread.start()
    assert started.wait(timeout=1)
    tracker_cache.invalidate_issue("ROBOPARK-1", membership_changed=False)
    release.set()
    thread.join(timeout=2)

    assert _search() == [_raw_issue("ROBOPARK-fresh")]
    assert calls == 2


def test_native_robot_source_reuses_only_exact_fresh_complete_registry_batch(monkeypatch):
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: [_registry_issue()])
    tracker_cache.search_issues(
        token="registry-token",
        query='(Queue: "ROBOMAINT" OR Queue: "ROBOPARK")',
        filter_open=False,
    )

    source = tracker_cache.peek_native_robot_source(("ROBOPARK", "ROBOMAINT"))

    assert source == [
        {
            "key": "ROBOPARK-1",
            "summary": "[a447] repair",
            "description": "Broken wheel",
            "status": {"key": "open", "display": "Open"},
            "type": {"key": "repair", "display": "Repair"},
            "tags": ["Alpha"],
            "resolution": {"key": "", "display": ""},
            "priority": {"key": "blocker", "display": "Blocker"},
            "rover": "a447",
            "statusStartTime": "2026-10-01T11:00:00Z",
            "homePort": "Alpha garage",
            "createdAt": "2026-10-01T10:00:00Z",
            "updatedAt": "2026-10-02T10:00:00Z",
            "resolvedAt": "",
        }
    ]
    assert tracker_cache.peek_native_robot_source(("FOREIGN",)) is None
    monkeypatch.setattr(tracker_cache._issues_cache, "_ttl", 0.01)
    time.sleep(0.02)
    assert tracker_cache.peek_native_robot_source(("ROBOPARK", "ROBOMAINT")) is None


@pytest.mark.parametrize(
    "missing", ["resolution_key", "rover", "priority_key", "status_start_time", "home_port"]
)
def test_native_robot_source_rejects_legacy_incomplete_registry_dto(monkeypatch, missing):
    incomplete = _registry_issue()
    incomplete.pop(missing)
    monkeypatch.setattr(tracker_client, "search_issues", lambda **_kwargs: [incomplete])
    tracker_cache.search_issues(
        token="registry-token",
        query='(Queue: "ROBOPARK")',
        filter_open=False,
    )

    assert tracker_cache.peek_native_robot_source(("ROBOPARK",)) is None


def test_issue_dto_retains_native_robot_classification_fields():
    raw = _raw_issue()
    raw["statusStartTime"] = "2026-10-01T11:00:00Z"
    raw["homePort"] = "Alpha garage"
    issue = tracker_client.issue_to_dict(raw)

    assert issue["resolution_key"] == ""
    assert issue["priority_key"] == "blocker"
    assert issue["rover"] == ["a447"]
    assert issue["status_start_time"] == "2026-10-01T11:00:00Z"
    assert issue["home_port"] == "Alpha garage"


def test_registry_snapshot_strips_sdk_resources_before_copying(monkeypatch):
    issue = _registry_issue(_tracker_resource=threading.Lock())
    monkeypatch.setattr(tracker_client, "search_issues", lambda **kwargs: [issue])
    tracker_cache.search_issues(token="token", query='(Queue: "ROBOPARK")', filter_open=False)
    source = tracker_cache.peek_robot_source(("ROBOPARK",))
    assert source and "_tracker_resource" not in source[0]
    source[0]["tags"].append("changed")
    assert tracker_cache.peek_robot_source(("ROBOPARK",))[0]["tags"] == ["Alpha"]
