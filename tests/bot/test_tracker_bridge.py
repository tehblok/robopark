"""The transition bot reads Tracker only through Robopark's internal API."""

from __future__ import annotations

import importlib
import secrets
from pathlib import Path

import requests

APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


class _Response:
    def __init__(self, value, status_code: int = 200):
        self.value = value
        self.status_code = status_code
        self.ok = 200 <= status_code < 300

    def json(self):
        return self.value


def _tracker_api(monkeypatch, tmp_path: Path):
    key = secrets.token_urlsafe(32)
    key_file = tmp_path / "bridge-key"
    key_file.write_text(key)
    key_file.chmod(0o600)
    monkeypatch.setenv("ROBOPARK_BOT_BRIDGE_KEY_FILE", str(key_file))
    monkeypatch.setenv("ROBOPARK_BOT_API_BASE", "http://api:8000/internal/bot/tracker")
    monkeypatch.syspath_prepend(str(APP))
    return importlib.import_module("tracker_api"), key


def test_search_uses_robopark_gateway_without_tracker_token(monkeypatch, tmp_path):
    tracker_api, key = _tracker_api(monkeypatch, tmp_path)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _Response([{"key": "TEST-1", "summary": "repair"}])

    monkeypatch.setattr(requests, "request", request)
    issues, error = tracker_api.search_issues("Queue: TEST", max_pages=1)

    assert error is None
    assert issues == [{"key": "TEST-1", "summary": "repair"}]
    assert len(calls) == 1
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url == "http://api:8000/internal/bot/tracker/search"
    assert kwargs["headers"]["X-Robopark-Bot-Key"] == key
    assert kwargs["json"]["query"] == "Queue: TEST"
    assert kwargs["timeout"] > 30


def test_issue_related_reads_use_gateway(monkeypatch, tmp_path):
    tracker_api, _key = _tracker_api(monkeypatch, tmp_path)
    calls = []
    answers = [
        {"key": "TEST-1", "status": {"key": "queued"}},
        [{"type": "relates"}],
        [{"updatedAt": "2026-09-28T09:00:00Z", "fields": []}],
    ]

    def request(method, url, **kwargs):
        calls.append((method, url))
        return _Response(answers[len(calls) - 1])

    monkeypatch.setattr(requests, "request", request)
    assert tracker_api.get_issue("TEST-1") == (answers[0], None)
    assert tracker_api.get_issue_links("TEST-1") == (answers[1], None)
    assert tracker_api.get_status_changelog("TEST-1") == answers[2]
    assert calls == [
        ("GET", "http://api:8000/internal/bot/tracker/issue/TEST-1"),
        ("GET", "http://api:8000/internal/bot/tracker/issue/TEST-1/links"),
        ("GET", "http://api:8000/internal/bot/tracker/issue/TEST-1/status-changelog"),
    ]


def test_gateway_failure_is_an_error_not_empty_data(monkeypatch, tmp_path):
    tracker_api, _key = _tracker_api(monkeypatch, tmp_path)
    monkeypatch.setattr(
        requests, "request", lambda *_args, **_kwargs: _Response({"detail": "upstream"}, 503),
    )

    issues, error = tracker_api.search_issues("Queue: TEST")
    assert issues == []
    assert error is not None and "503" in error
