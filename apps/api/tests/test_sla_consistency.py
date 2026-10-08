"""One confirmed first queue anchor feeds cards, overview and Telegram reports."""

from datetime import UTC, datetime

import pytest

from robopark_api.routers.tracker_read import _issue_out
from robopark_api.services import native_telegram_reports, operations, tracker_history


@pytest.mark.parametrize(
    ("timezone", "expected_hours", "expected_deadline"),
    [
        ("Europe/Moscow", 0.5, "2026-10-09T10:30:00Z"),
        ("Asia/Yekaterinburg", 1.5, "2026-10-09T09:00:00Z"),
    ],
)
def test_first_queue_with_service_tag_is_shared_across_all_surfaces(
    db_session, seed_park_with_tracker, timezone, expected_hours, expected_deadline
):
    park = seed_park_with_tracker
    park.timezone = timezone
    db_session.commit()
    key = f"{park.tracker_queue}-SLA"
    first = "2026-10-08T17:30:00Z"
    latest = "2026-10-09T04:30:00Z"
    tags = [park.tag, "waiting_complete"]
    history = [
        {
            "id": "first-queue",
            "updatedAt": first,
            "fields": [
                {"field": {"id": "status"}, "from": {"key": "moving"}, "to": {"key": "queued"}},
                {"field": {"id": "tags"}, "from": [park.tag], "to": tags},
            ],
        },
        {
            "id": "leave",
            "updatedAt": "2026-10-08T17:45:00Z",
            "fields": [
                {"field": {"id": "status"}, "from": {"key": "queued"}, "to": {"key": "new"}}
            ],
        },
        {
            "id": "requeue",
            "updatedAt": latest,
            "fields": [
                {"field": {"id": "status"}, "from": {"key": "new"}, "to": {"key": "queued"}}
            ],
        },
    ]
    state = tracker_history.ingest_status_history(
        db_session, issue_key=key, park=park, history=history, current_tags=set(tags)
    )
    assert state.anchor_timezone == timezone
    now = datetime(2026, 10, 9, 5, 30, tzinfo=UTC)
    current_timezone = "Asia/Yekaterinburg" if timezone == "Europe/Moscow" else "Europe/Moscow"
    issue = {"key": key, "status_key": "queued", "queue": park.tracker_queue, "tags": tags}
    verified = tracker_history.attach_verified_history(db_session, [issue])[0]
    card = _issue_out(verified, timezone=current_timezone)
    overview = operations.task_timing(verified, now, timezone=current_timezone)
    bot = native_telegram_reports.enrich(
        db_session,
        [{**issue, "status": {"key": "queued"}, "statusStartTime": latest}],
        now=now,
        timezone=current_timezone,
    )["issues"][0]["bot_report"]

    expected_start = datetime.fromisoformat(first.replace("Z", "+00:00"))
    expected_due = datetime.fromisoformat(expected_deadline.replace("Z", "+00:00"))
    assert datetime.fromisoformat(card.queued_at.replace("Z", "+00:00")) == expected_start
    assert datetime.fromisoformat(card.sla_deadline.replace("Z", "+00:00")) == expected_due
    assert card.sla_timezone == overview.sla_timezone == timezone
    assert overview.queue_started_at == expected_start
    assert overview.sla_deadline == expected_due
    assert overview.sla_working_hours == bot["repair_hours"] == bot["sla_hours"] == expected_hours
    assert bot["sla_overdue"] is False
