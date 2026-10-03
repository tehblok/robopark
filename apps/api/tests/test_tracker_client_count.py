from unittest.mock import MagicMock

import pytest

from robopark_api.services.tracker_client import TrackerError, count_issues, issue_to_dict


def test_closed_history_page_reads_only_fields_needed_for_status_import(monkeypatch):
    from robopark_api.services import tracker_client

    calls = []

    class FakeIssues:
        def find(self, query, **kwargs):
            calls.append((query, kwargs))
            return [
                {
                    "key": "RP-1",
                    "queue": {"key": "ROBOPARK"},
                    "tags": ["Alpha"],
                    "status": {"key": "closed", "display": "Закрыт"},
                    "updatedAt": "2026-09-25T10:00:00Z",
                    "summary": "Must not enter the DTO",
                }
            ]

    monkeypatch.setattr(
        tracker_client, "_client", lambda token: type("Client", (), {"issues": FakeIssues()})()
    )
    monkeypatch.setattr(tracker_client, "_run_tracked", lambda fn, **kwargs: fn())
    result = tracker_client.search_closed_history_page(
        token="test-token", query="Queue: ROBOPARK", page=3
    )
    assert calls == [
        (
            "Queue: ROBOPARK",
            {
                "per_page": 50,
                "page": 3,
                "fields": "key,queue,tags,status,updatedAt",
            },
        )
    ]
    assert [
        {key: value for key, value in item.items() if key != "_tracker_resource"} for item in result
    ] == [
        {
            "key": "RP-1",
            "queue": "ROBOPARK",
            "tags": ["Alpha"],
            "status_key": "closed",
            "status": "Закрыт",
            "updated": "2026-09-25T10:00:00Z",
        }
    ]
    assert len(result) == 1


def test_issue_to_dict_includes_queue():
    item = issue_to_dict(
        {
            "key": "ROB-1",
            "summary": "test",
            "queue": {"key": "ROBOPARK", "display": "Robopark"},
            "status": {"key": "queued", "display": "В очереди"},
        }
    )
    assert item["queue"] == "ROBOPARK"


def test_count_issues_parses_int_dict_and_value_attr(monkeypatch):
    class FakeIssues:
        def __init__(self, result):
            self._result = result

        def find(self, *_args, **_kwargs):
            return self._result

    class FakeClient:
        def __init__(self, result):
            self.issues = FakeIssues(result)

    cases = [
        (7, 7),
        ({"count": 3}, 3),
        ({"total": "4"}, 4),
        (type("Wrap", (), {"_value": 9})(), 9),
    ]
    for payload, expected in cases:
        monkeypatch.setattr(
            "robopark_api.services.tracker_client._client",
            lambda _token, result=payload: FakeClient(result),
        )
        monkeypatch.setattr(
            "robopark_api.services.tracker_client.call_with_retry",
            lambda fn, **_kwargs: fn(),
        )
        assert count_issues(token="t", query="q") == expected


def test_count_issues_falls_back_to_pagination(monkeypatch):
    class FakeIssues:
        def find(self, *_args, **kwargs):
            if kwargs.get("count_only"):
                return {"weird": True}
            return [object(), object(), object()]

    class FakeClient:
        def __init__(self):
            self.issues = FakeIssues()

    monkeypatch.setattr(
        "robopark_api.services.tracker_client._client",
        lambda _token: FakeClient(),
    )
    monkeypatch.setattr(
        "robopark_api.services.tracker_client.call_with_retry",
        lambda fn, **_kwargs: fn(),
    )
    assert count_issues(token="t", query="q") == 3


def test_count_issues_maps_upstream_errors(monkeypatch):
    monkeypatch.setattr(
        "robopark_api.services.tracker_client._client",
        lambda _token: MagicMock(),
    )

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(
        "robopark_api.services.tracker_client.call_with_retry",
        lambda fn, **_kwargs: boom(),
    )
    with pytest.raises(TrackerError, match="network down"):
        count_issues(token="t", query="q")
