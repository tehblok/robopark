from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from robopark_api.models import TrackerIssueHistoryState, TrackerIssueStatusEvent
from robopark_api.services import native_telegram_reports

NOW = datetime(2026, 10, 7, 16, tzinfo=UTC)


def _history(
    db_session,
    key: str,
    queued_at: datetime,
    *,
    ended_at: datetime | None = None,
    timezone: str | None = "Europe/Moscow",
) -> None:
    db_session.add(
        TrackerIssueHistoryState(
            issue_key=key,
            first_queued_at=queued_at,
            anchor_timezone=timezone,
            history_state="complete",
        )
    )
    db_session.flush()
    db_session.add(
        TrackerIssueStatusEvent(
            issue_key=key,
            event_key=f"{key}-queued",
            occurred_at=queued_at,
            from_status_key="diagnostics",
            to_status_key="queued",
            to_status_display="В очереди",
        )
    )
    if ended_at is not None:
        db_session.add(
            TrackerIssueStatusEvent(
                issue_key=key,
                event_key=f"{key}-ended",
                occurred_at=ended_at,
                from_status_key="queued",
                to_status_key="waitingForAnotherTeam",
                to_status_display="Ждем смежников",
            )
        )
    db_session.commit()


def test_first_queue_anchor_uses_legacy_thresholds_and_does_not_freeze_at_exit(db_session):
    _history(db_session, "ROBOPARK-1", NOW - timedelta(hours=6))
    _history(
        db_session,
        "ROBOPARK-4",
        NOW - timedelta(hours=12),
        ended_at=NOW - timedelta(hours=8),
    )

    result = native_telegram_reports.enrich(
        db_session,
        [
            {
                "key": "ROBOPARK-1",
                "summary": "Repair rover",
                "status": {"key": "queued"},
                "createdAt": "2026-10-07T06:00:00Z",
            },
            {
                "key": "ROBOPARK-4",
                "summary": "Repair rover",
                "status": {"key": "waitingForAnotherTeam"},
                "createdAt": "2026-10-07T06:00:00Z",
            },
        ],
        timezone="Asia/Yekaterinburg",
        now=NOW,
    )

    overdue = result["issues"][0]["bot_report"]
    assert overdue["repair_hours"] == 6.0
    assert overdue["downtime_hours"] == 10.0
    assert overdue["repair_source"] == "queued_history"
    assert overdue["sla_overdue"] is True
    frozen = result["issues"][1]["bot_report"]
    assert frozen["repair_hours"] == 10.0
    assert frozen["sla_at_risk"] is False
    assert frozen["sla_overdue"] is True
    assert result["summary"] == {
        "sla_target_hours": 5,
        "sla_at_risk_hours": 3.0,
        "sla_evaluated": 2,
        "sla_unknown": 0,
        "sla_at_risk": 0,
        "sla_overdue": 2,
        "log_dump": 0,
    }


@pytest.mark.parametrize("timezone", ["Asia/Yekaterinburg", "Asia/Almaty"])
def test_first_queue_uses_anchor_timezone_and_pauses_during_local_night(db_session, timezone):
    queued_at = datetime(2026, 10, 6, 15, tzinfo=UTC)
    _history(db_session, "ROBOPARK-LOCAL", queued_at, timezone=timezone)
    report = native_telegram_reports.enrich(
        db_session,
        [
            {
                "key": "ROBOPARK-LOCAL",
                "status": {"key": "queued"},
                "createdAt": queued_at.isoformat(),
            }
        ],
        timezone="Europe/Moscow",
        now=datetime(2026, 10, 7, 6, tzinfo=UTC),
    )["issues"][0]["bot_report"]

    assert report["repair_hours"] == 3
    assert report["downtime_hours"] == 15


def test_repeat_queue_does_not_reset_first_confirmed_anchor(db_session):
    first = datetime(2026, 10, 7, 9, tzinfo=UTC)
    _history(db_session, "ROBOPARK-REQUEUE", first, timezone="UTC")
    db_session.add(
        TrackerIssueStatusEvent(
            issue_key="ROBOPARK-REQUEUE",
            event_key="ROBOPARK-REQUEUE-again",
            occurred_at=datetime(2026, 10, 7, 14, tzinfo=UTC),
            from_status_key="inProgress",
            to_status_key="queued",
            to_status_display="В очереди",
        )
    )
    db_session.commit()
    report = native_telegram_reports.enrich(
        db_session,
        [
            {
                "key": "ROBOPARK-REQUEUE",
                "status": {"key": "queued"},
                "createdAt": "2026-10-07T08:00:00Z",
                "statusStartTime": "2026-10-07T14:00:00Z",
            }
        ],
        timezone="Asia/Almaty",
        now=NOW,
    )["issues"][0]["bot_report"]

    assert report["repair_hours"] == 7
    assert report["downtime_hours"] == 8


def test_missing_history_or_anchor_timezone_stays_unknown(db_session):
    _history(
        db_session,
        "ROBOPARK-NO-ZONE",
        datetime(2026, 10, 7, 10, tzinfo=UTC),
        timezone=None,
    )
    result = native_telegram_reports.enrich(
        db_session,
        [
            {
                "key": "ROBOPARK-2",
                "summary": "Repair rover",
                "status": {"key": "waitingForAnotherTeam"},
                "createdAt": "2026-10-07T10:00:00Z",
                "statusStartTime": "2026-10-07T09:00:00Z",
            },
            {
                "key": "ROBOPARK-OPEN",
                "summary": "Repair rover",
                "status": {"key": "open"},
                "createdAt": "2026-10-07T10:00:00Z",
            },
            {
                "key": "ROBOPARK-NO-ZONE",
                "summary": "Repair rover",
                "status": {"key": "queued"},
                "createdAt": "2026-10-07T10:00:00Z",
            },
            {"key": "ROBOPARK-LOG", "summary": "Run log_dump for rover"},
        ],
        timezone="UTC",
        now=NOW,
    )

    missing = result["issues"][0]["bot_report"]
    assert missing["repair_hours"] is None
    assert missing["downtime_hours"] == 6.0
    assert missing["sla_overdue"] is None
    opened = result["issues"][1]["bot_report"]
    assert opened["repair_hours"] is None
    assert opened["repair_source"] is None
    assert opened["sla_overdue"] is None
    no_zone = result["issues"][2]["bot_report"]
    assert no_zone["repair_hours"] is None
    assert no_zone["sla_overdue"] is None
    log_dump = result["issues"][3]["bot_report"]
    assert log_dump["log_dump"] is True
    assert log_dump["log_dump_source"] == "summary"
    assert log_dump["sla_overdue"] is None
    assert result["summary"]["sla_unknown"] == 3
    assert result["summary"]["log_dump"] == 1


def test_invalid_persisted_timezone_is_unknown_without_breaking_other_tasks(db_session):
    queued = datetime(2026, 10, 7, 10, tzinfo=UTC)
    _history(db_session, "ROBOPARK-BAD-ZONE", queued, timezone="Invalid/Timezone")
    _history(db_session, "ROBOPARK-GOOD-ZONE", queued, timezone="UTC")

    result = native_telegram_reports.enrich(
        db_session,
        [
            {"key": "ROBOPARK-BAD-ZONE", "status": {"key": "queued"}},
            {"key": "ROBOPARK-GOOD-ZONE", "status": {"key": "queued"}},
        ],
        timezone="Europe/Moscow",
        now=NOW,
    )

    invalid = result["issues"][0]["bot_report"]
    valid = result["issues"][1]["bot_report"]
    assert invalid["repair_hours"] is None
    assert invalid["sla_hours"] is None
    assert invalid["sla_overdue"] is None
    assert valid["repair_hours"] == 6
    assert valid["sla_overdue"] is True
    assert result["summary"]["sla_unknown"] == 1
    assert result["summary"]["sla_evaluated"] == 1


def test_future_persisted_queue_event_is_treated_as_unknown(db_session):
    _history(db_session, "ROBOPARK-3", NOW + timedelta(minutes=1))

    report = native_telegram_reports.enrich(
        db_session,
        [
            {
                "key": "ROBOPARK-3",
                "summary": "Repair rover",
                "status": {"key": "queued"},
            }
        ],
        timezone="UTC",
        now=NOW,
    )["issues"][0]["bot_report"]

    assert report["repair_hours"] is None
    assert report["sla_overdue"] is None


def test_sla_is_overdue_after_deadline_during_night_even_at_exactly_five_hours(db_session):
    queued = datetime(2026, 10, 7, 16, tzinfo=UTC)
    _history(db_session, "ROBOPARK-NIGHT-DEADLINE", queued, timezone="UTC")

    report = native_telegram_reports.enrich(
        db_session,
        [{"key": "ROBOPARK-NIGHT-DEADLINE", "status": {"key": "queued"}}],
        timezone="Asia/Almaty",
        now=datetime(2026, 10, 7, 22, tzinfo=UTC),
    )["issues"][0]["bot_report"]

    assert report["repair_hours"] == 5
    assert report["sla_overdue"] is True
    assert report["sla_at_risk"] is False


@pytest.mark.parametrize(
    ("issue", "source"),
    [
        ({"summary": "Run log_dump for rover"}, "summary"),
        ({"summary": "Disk space by volume"}, "summary"),
        ({"summary": "Routine task", "tags": ["log_dump"]}, "tag"),
        ({"summary": "Routine task", "type": {"key": "log-dump"}}, "type"),
        ({"summary": "catalog_dump processing"}, None),
        ({"summary": "log_dumping still running"}, None),
        ({"summary": "Disk space byte alert"}, None),
        ({"summary": "Routine task", "tags": ["not-log_dump"]}, None),
    ],
)
def test_log_dump_uses_exact_markers_and_bounded_legacy_phrases(issue, source):
    assert native_telegram_reports.log_dump_source(issue) == source


def test_enrichment_caps_at_500_and_reads_history_anchors_once(db_session):
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if "tracker_issue_history_state" in statement.lower():
            statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", capture)
    try:
        result = native_telegram_reports.enrich(
            db_session,
            [{"key": f"ROBOPARK-{index}", "summary": "Repair"} for index in range(501)],
            timezone="UTC",
            now=NOW,
        )
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", capture)

    assert len(result["issues"]) == 500
    assert len(statements) == 1
    assert result["summary"]["sla_unknown"] == 500


def test_current_queued_status_start_is_not_used_without_verified_history(db_session):
    report = native_telegram_reports.enrich(
        db_session,
        [
            {
                "key": "ROBOPARK-22",
                "status": {"key": "queued"},
                "createdAt": "2026-10-01T00:00:00Z",
                "statusStartTime": "2026-10-07T10:00:00Z",
            }
        ],
        timezone="Europe/Moscow",
        now=NOW,
    )
    assert report["issues"][0]["bot_report"]["repair_hours"] is None
    assert report["issues"][0]["bot_report"]["repair_source"] is None


def test_report_preparation_loads_latest_changelog_and_persists_verified_interval(
    db_session, seed_park_with_tracker, monkeypatch
):
    from concurrent.futures import Future

    from robopark_api.services import tracker_client

    future = Future()
    future.set_result(
        [
            {
                "id": "start",
                "updatedAt": "2026-10-07T09:00:00Z",
                "fields": [
                    {"field": {"id": "status"}, "from": {"key": "open"}, "to": {"key": "queued"}}
                ],
            },
            {
                "id": "end",
                "updatedAt": "2026-10-07T13:00:00Z",
                "fields": [
                    {
                        "field": {"id": "status"},
                        "from": {"key": "queued"},
                        "to": {"key": "waitingForAnotherTeam"},
                    }
                ],
            },
        ]
    )
    calls = []

    def schedule(**kwargs):
        calls.append(kwargs)
        return future, True

    monkeypatch.setattr(tracker_client, "schedule_issue_status_history", schedule)
    issues = [
        {
            "key": "ROBOPARK-22",
            "status": {"key": "queued"},
            "tags": [seed_park_with_tracker.tag],
            "statusStartTime": "2026-10-07T12:00:00Z",
            "updatedAt": "2026-10-07T13:00:00Z",
        }
    ]
    native_telegram_reports.prepare_history(
        db_session, token="test", park=seed_park_with_tracker, issues=issues
    )
    report = native_telegram_reports.enrich(db_session, issues, timezone="Europe/Moscow", now=NOW)
    assert report["issues"][0]["bot_report"]["repair_hours"] == 7
    assert len(calls) == 1


def test_report_history_preparation_bounds_admissions_and_does_not_wait_for_stuck_read(
    db_session, seed_park_with_tracker, monkeypatch
):
    from concurrent.futures import Future
    from time import monotonic

    from robopark_api.services import tracker_client

    calls = []

    def schedule(**kwargs):
        calls.append(kwargs)
        return Future(), True

    monkeypatch.setattr(tracker_client, "schedule_issue_status_history", schedule)
    issues = [
        {"key": f"ROBOPARK-{n}", "status": {"key": "waitingForAnotherTeam"}} for n in range(500)
    ]
    start = monotonic()
    native_telegram_reports.prepare_history(
        db_session,
        token="test",
        park=seed_park_with_tracker,
        issues=issues,
        budget_seconds=0.01,
        max_starts=2,
    )
    assert monotonic() - start < 0.5
    assert sum(row["allow_start"] for row in calls) == 2


def test_report_preparation_skips_confirmed_anchor_but_not_queued_status_start(
    db_session, seed_park_with_tracker, monkeypatch
):
    from concurrent.futures import Future

    from robopark_api.services import tracker_client

    _history(db_session, "ROBOPARK-CONFIRMED", NOW - timedelta(hours=2))
    calls = []

    def schedule(**kwargs):
        calls.append(kwargs)
        future = Future()
        future.set_result([])
        return future, True

    monkeypatch.setattr(tracker_client, "schedule_issue_status_history", schedule)
    native_telegram_reports.prepare_history(
        db_session,
        token="test",
        park=seed_park_with_tracker,
        issues=[
            {"key": "ROBOPARK-CONFIRMED", "status": {"key": "queued"}},
            {
                "key": "ROBOPARK-NEEDS-HISTORY",
                "status": {"key": "queued"},
                "statusStartTime": "2026-10-07T14:00:00Z",
            },
        ],
    )

    assert [call["key"] for call in calls] == ["ROBOPARK-NEEDS-HISTORY"]


def test_report_preparation_fetches_open_history_and_keeps_no_queue_unknown(
    db_session, seed_park_with_tracker, monkeypatch
):
    from concurrent.futures import Future

    from robopark_api.services import tracker_client

    future = Future()
    future.set_result(
        [
            {
                "id": "opened",
                "updatedAt": "2026-10-07T09:00:00Z",
                "fields": [
                    {
                        "field": {"id": "status"},
                        "from": {"key": "draft"},
                        "to": {"key": "open"},
                    }
                ],
            }
        ]
    )
    calls = []

    def schedule(**kwargs):
        calls.append(kwargs)
        return future, True

    monkeypatch.setattr(tracker_client, "schedule_issue_status_history", schedule)
    issue = {
        "key": "ROBOPARK-OPEN-NO-QUEUE",
        "status": {"key": "open"},
        "tags": [seed_park_with_tracker.tag],
        "createdAt": "2026-10-07T08:00:00Z",
    }

    native_telegram_reports.prepare_history(
        db_session, token="test", park=seed_park_with_tracker, issues=[issue]
    )
    report = native_telegram_reports.enrich(
        db_session, [issue], timezone=seed_park_with_tracker.timezone, now=NOW
    )["issues"][0]["bot_report"]

    assert [call["key"] for call in calls] == ["ROBOPARK-OPEN-NO-QUEUE"]
    assert db_session.get(TrackerIssueHistoryState, "ROBOPARK-OPEN-NO-QUEUE").history_state == (
        "no_queue"
    )
    assert report["repair_hours"] is None
    assert report["sla_overdue"] is None
