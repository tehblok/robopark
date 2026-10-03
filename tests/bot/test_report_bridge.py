"""The report uses the Robopark gateway without a second Tracker token."""

from __future__ import annotations

import importlib
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_report_collect_does_not_require_bot_tracker_token(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(APP))
    monkeypatch.delenv("TRACKER_TOKEN", raising=False)
    report = importlib.import_module("report")
    monkeypatch.setattr(report, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(report, "ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(report, "LATEST_FILE", str(tmp_path / "latest_report.csv"))
    calls = []

    def search(*args, **kwargs):
        calls.append((args, kwargs))
        return [], None

    monkeypatch.setattr(report, "search_issues", search)
    assert report.collect() is True
    assert len(calls) == 1
    assert (tmp_path / "latest_report.csv").is_file()


def test_report_reuses_queue_period_for_unchanged_status(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(APP))
    report = importlib.import_module("report")
    monkeypatch.setattr(report, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(report, "ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(report, "LATEST_FILE", str(tmp_path / "latest_report.csv"))
    issue = {
        "key": "SDCFLEETOPS-1", "summary": "Repair", "status": {"key": "inProgress"},
        "statusStartTime": "2026-09-28T10:00:00Z", "tags": [],
    }
    monkeypatch.setattr(report, "search_issues", lambda *_args, **_kwargs: ([issue], None))
    calls = []

    def changelog(_key):
        calls.append(1)
        return [{
            "updatedAt": "2026-09-28T09:00:00Z",
            "fields": [{"field": {"id": "status"}, "from": {"key": "new"}, "to": {"key": "queued"}}],
        }]

    monkeypatch.setattr(report, "get_status_changelog", changelog)
    assert report.collect() is True
    assert report.collect() is True
    assert len(calls) == 1

    issue["statusStartTime"] = "2026-09-28T11:00:00Z"
    assert report.collect() is True
    assert len(calls) == 2
