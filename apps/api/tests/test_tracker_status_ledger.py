"""Tracker status evidence is durable across polling and reopening."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from robopark_api.models import Park, TrackerIssueHistoryState, TrackerIssueStatusEvent
from robopark_api.services import platform_settings, tracker_client, tracker_history


@pytest.fixture(autouse=True)
def no_external_closed_history_search(monkeypatch):
    monkeypatch.setattr(tracker_client, "search_closed_history_page", lambda **kwargs: [])


def change(at: datetime, key: str, display: str) -> dict:
    return {
        "updatedAt": at.isoformat(),
        "fields": [{"field": {"id": "status"}, "to": {"key": key, "display": display}}],
    }


def test_first_queue_survives_close_reopen_and_repeated_ingest(db_session, seed_park_with_tracker):
    park = seed_park_with_tracker
    queued = datetime(2026, 9, 25, 7, 0, tzinfo=UTC)
    first = [
        change(queued, "queued", "В очереди"),
        change(queued + timedelta(hours=1), "inProgress", "В работе"),
        change(queued + timedelta(hours=3), "closed", "Закрыт"),
    ]
    tracker_history.ingest_status_history(db_session, issue_key="RP-1", park=park, history=first)
    tracker_history.ingest_status_history(db_session, issue_key="RP-1", park=park, history=first)
    state = db_session.get(TrackerIssueHistoryState, "RP-1")
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.terminal_at.replace(tzinfo=UTC) == queued + timedelta(hours=3)
    assert len(db_session.scalars(select(TrackerIssueStatusEvent)).all()) == 3

    reopened = first + [
        change(queued + timedelta(days=2), "inProgress", "В работе"),
        change(queued + timedelta(days=2, hours=1), "queued", "В очереди"),
        change(queued + timedelta(days=2, hours=4), "closed", "Закрыт"),
    ]
    tracker_history.ingest_status_history(
        db_session, issue_key="RP-1", park=park, history=list(reversed(reopened))
    )
    db_session.refresh(state)
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.terminal_at.replace(tzinfo=UTC) == queued + timedelta(days=2, hours=4)
    assert state.history_state == "complete"
    assert len(db_session.scalars(select(TrackerIssueStatusEvent)).all()) == 6


def test_repeated_history_read_keeps_timezone_recorded_at_first_queue(
    db_session, seed_park_with_tracker
):
    park = seed_park_with_tracker
    queued = datetime(2026, 9, 25, 17, tzinfo=UTC)
    history = [change(queued, "queued", "В очереди")]
    state = tracker_history.ingest_status_history(
        db_session, issue_key="RP-ZONE", park=park, history=history
    )
    assert state.anchor_timezone == "Europe/Moscow"

    park.timezone = "Asia/Yekaterinburg"
    db_session.commit()
    tracker_history.ingest_status_history(
        db_session, issue_key="RP-ZONE", park=park, history=history
    )
    db_session.refresh(state)
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id == park.id
    assert state.anchor_timezone == "Europe/Moscow"


def test_transfer_preserves_first_queue_sla_but_attributes_closure_to_destination(
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.services.analytics import verified_closures

    origin = seed_park_with_tracker
    destination = Park(
        name="South",
        tag="South",
        timezone="Asia/Yekaterinburg",
        tracker_queue=origin.tracker_queue,
        is_active=True,
    )
    db_session.add(destination)
    db_session.commit()
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    closed = queued + timedelta(hours=3)
    projected = tracker_client._project_issue_status_history(
        [
            {**change(queued, "queued", "В очереди"), "id": "queued"},
            {
                "id": "transfer",
                "updatedAt": (queued + timedelta(hours=1)).isoformat(),
                "fields": [
                    {
                        "field": {"id": "tags"},
                        "from": [{"id": "tag-1", "display": origin.tag}],
                        "to": [{"id": "tag-2", "display": destination.tag}],
                    }
                ],
            },
            {**change(closed, "closed", "Закрыт"), "id": "closed"},
        ]
    )
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-TRANSFER",
        park=destination,
        history=projected,
    )

    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id == origin.id
    assert state.anchor_timezone == origin.timezone
    assert (
        verified_closures(
            db_session,
            park_id=origin.id,
            start=queued,
            end=closed + timedelta(hours=1),
            scoped=False,
        ).count
        == 0
    )
    destination_closures = verified_closures(
        db_session,
        park_id=destination.id,
        start=queued,
        end=closed + timedelta(hours=1),
        scoped=False,
    )
    assert destination_closures.count == 1
    assert destination_closures.sla_on_time_count == 1


def test_tag_and_status_changed_together_keep_queue_park_unknown(
    db_session,
    seed_park_with_tracker,
):
    origin = seed_park_with_tracker
    destination = Park(
        name="South",
        tag="South",
        tracker_queue=origin.tracker_queue,
        is_active=True,
    )
    db_session.add(destination)
    db_session.commit()
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    projected = tracker_client._project_issue_status_history(
        [
            {
                "id": "same-change",
                "updatedAt": queued.isoformat(),
                "fields": [
                    {
                        "field": {"id": "tags"},
                        "from": [{"id": origin.tag}],
                        "to": [{"id": destination.tag}],
                    },
                    {"field": {"id": "status"}, "to": {"key": "queued", "display": "В очереди"}},
                ],
            }
        ]
    )
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-SAME-CHANGE",
        park=destination,
        history=projected,
    )

    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id is None
    assert state.anchor_timezone is None
    assert db_session.scalar(select(TrackerIssueStatusEvent.park_id)) is None
    projected_issue = tracker_history.attach_verified_history(
        db_session, [{"key": "RP-SAME-CHANGE", "status_key": "queued"}]
    )[0]
    assert projected_issue["queued_at"] == "2026-09-25T07:00:00Z"
    assert "sla_anchor_timezone" in projected_issue
    assert projected_issue["sla_anchor_timezone"] is None


@pytest.mark.parametrize(
    ("before_tags", "after_tags"),
    [
        (
            ["SUF", "order_no", "Moscow", "taxipark"],
            ["SUF", "order_no", "Moscow", "taxipark", "waiting_complete"],
        ),
        (["Moscow", "waiting_complete"], ["Moscow"]),
    ],
)
def test_atomic_status_and_unrelated_tag_change_keeps_unchanged_park_evidence(
    db_session,
    seed_park_with_tracker,
    before_tags,
    after_tags,
):
    park = seed_park_with_tracker
    park.tag = "Moscow"
    db_session.commit()
    queued = datetime(2026, 9, 25, 9, 54, tzinfo=UTC)
    projected = tracker_client._project_issue_status_history(
        [
            {
                "id": "queue-and-service-tag",
                "updatedAt": queued.isoformat(),
                "fields": [
                    {
                        "field": {"id": "tags"},
                        "from": before_tags,
                        "to": after_tags,
                    },
                    {
                        "field": {"id": "status"},
                        "to": {"key": "queued", "display": "В очереди"},
                    },
                ],
            }
        ]
    )

    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-ATOMIC-SERVICE-TAG",
        park=park,
        history=projected,
        current_tags=set(after_tags),
    )

    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id == park.id
    assert state.anchor_timezone == "Europe/Moscow"
    assert db_session.scalar(select(TrackerIssueStatusEvent.park_id)) == park.id


def test_separate_same_time_status_and_tag_records_keep_queue_park_unknown(
    db_session,
    seed_park_with_tracker,
):
    park = seed_park_with_tracker
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    projected = tracker_client._project_issue_status_history(
        [
            {
                "id": "tag-change",
                "updatedAt": queued.isoformat(),
                "fields": [
                    {
                        "field": {"id": "tags"},
                        "from": [park.tag, "waiting_complete"],
                        "to": [park.tag],
                    }
                ],
            },
            {**change(queued, "queued", "В очереди"), "id": "status-change"},
        ]
    )

    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-SEPARATE-SAME-TIME",
        park=park,
        history=projected,
        current_tags={park.tag},
    )

    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id is None
    assert state.anchor_timezone is None
    assert db_session.scalar(select(TrackerIssueStatusEvent.park_id)) is None


def test_same_timestamp_tag_transfers_do_not_invent_historical_park(
    db_session,
    seed_park_with_tracker,
):
    origin = seed_park_with_tracker
    destination = Park(
        name="South",
        tag="South",
        tracker_queue=origin.tracker_queue,
        is_active=True,
    )
    db_session.add(destination)
    db_session.commit()
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    transfer_at = queued + timedelta(hours=1)
    history = [
        {**change(queued, "queued", "В очереди"), "id": "queued"},
        {
            "id": "a",
            "updatedAt": transfer_at.isoformat(),
            "fields": [
                {"field": {"id": "tags"}, "from": [origin.tag], "to": ["Middle"]},
            ],
        },
        {
            "id": "b",
            "updatedAt": transfer_at.isoformat(),
            "fields": [
                {"field": {"id": "tags"}, "from": ["Middle"], "to": [destination.tag]},
            ],
        },
    ]
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-UNORDERED",
        park=destination,
        history=tracker_client._project_issue_status_history(history),
    )

    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id is None


def test_missing_current_park_tags_do_not_attribute_status_by_requested_park(
    db_session,
    seed_park_with_tracker,
):
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-NO-TAGS",
        park=seed_park_with_tracker,
        current_tags=set(),
        history=[change(queued, "queued", "В очереди")],
    )
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id is None
    assert state.anchor_timezone is None


def test_later_park_assignment_starts_sla_at_queue_and_survives_transfer_and_reopen(
    db_session, seed_park_with_tracker
):
    origin = seed_park_with_tracker
    destination = Park(
        name="Later destination",
        tag="Later",
        timezone="Asia/Yekaterinburg",
        tracker_queue=origin.tracker_queue,
        is_active=True,
    )
    db_session.add(destination)
    db_session.commit()
    queued = datetime(2026, 9, 25, 17, tzinfo=UTC)
    history = [
        change(queued, "queued", "В очереди"),
        {
            "id": "assign",
            "updatedAt": (queued + timedelta(minutes=10)).isoformat(),
            "fields": [{"field": {"id": "tags"}, "from": [], "to": [origin.tag]}],
        },
    ]
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-LATE-PARK",
        park=origin,
        history=history,
        current_tags={origin.tag},
    )
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id == origin.id
    assert state.anchor_timezone == "Europe/Moscow"
    # The queue event remains honestly unattributed: the later assignment only anchors SLA.
    assert db_session.scalar(select(TrackerIssueStatusEvent.park_id)) is None
    from robopark_api.services.operations import task_timing

    card = tracker_history.attach_verified_history(db_session, [{"key": "RP-LATE-PARK"}])[0]
    timing = task_timing(card, datetime(2026, 9, 26, 10, tzinfo=UTC), timezone=destination.timezone)
    assert timing.sla_deadline == datetime(2026, 9, 26, 10, tzinfo=UTC)
    assert timing.sla_working_hours == 5
    assert timing.downtime_hours == 17

    history += [
        {
            "id": "transfer",
            "updatedAt": (queued + timedelta(hours=1)).isoformat(),
            "fields": [{"field": {"id": "tags"}, "from": [origin.tag], "to": [destination.tag]}],
        },
        change(queued + timedelta(hours=2), "closed", "Закрыт"),
        change(queued + timedelta(days=1), "queued", "В очереди"),
    ]
    origin.timezone = "Asia/Vladivostok"
    db_session.commit()
    tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-LATE-PARK",
        park=destination,
        history=list(reversed(history)),
        current_tags={destination.tag},
    )
    db_session.refresh(state)
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id == origin.id
    assert state.anchor_timezone == "Europe/Moscow"
    assert state.terminal_at is None


@pytest.mark.parametrize("evidence", ["different_queue", "multiple_parks", "malformed"])
def test_late_park_assignment_does_not_guess_across_ambiguous_evidence(
    db_session, seed_park_with_tracker, evidence
):
    origin = seed_park_with_tracker
    other = Park(name="Other", tag="duplicate", tracker_queue=origin.tracker_queue, is_active=True)
    db_session.add(other)
    db_session.commit()
    # A different Tracker queue must never provide this task's SLA timezone.
    if evidence == "different_queue":
        other.tracker_queue = "UNRELATED"
        db_session.commit()
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    first_tags = (
        ["duplicate"]
        if evidence == "different_queue"
        else [origin.tag, "duplicate"]
        if evidence == "multiple_parks"
        else None
    )
    history = [
        change(queued, "queued", "В очереди"),
        {
            "id": "assign",
            "updatedAt": (queued + timedelta(minutes=1)).isoformat(),
            "fields": [{"field": {"id": "tags"}, "from": [], "to": first_tags}],
        },
    ]
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-AMBIGUOUS-PARK",
        park=origin,
        history=history,
        current_tags=set(first_tags or []),
    )
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.anchor_park_id is None
    assert state.anchor_timezone is None


def test_first_ambiguous_park_assignment_is_not_replaced_by_later_unique_park(
    db_session, seed_park_with_tracker
):
    origin = seed_park_with_tracker
    other = Park(name="Other", tag="Other", tracker_queue=origin.tracker_queue, is_active=True)
    db_session.add(other)
    db_session.commit()
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    history = [
        change(queued, "queued", "В очереди"),
        {
            "id": "ambiguous",
            "updatedAt": (queued + timedelta(minutes=1)).isoformat(),
            "fields": [{"field": {"id": "tags"}, "from": [], "to": [origin.tag, other.tag]}],
        },
        {
            "id": "later",
            "updatedAt": (queued + timedelta(hours=1)).isoformat(),
            "fields": [
                {"field": {"id": "tags"}, "from": [origin.tag, other.tag], "to": [origin.tag]}
            ],
        },
    ]
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-FIRST-AMBIGUOUS",
        park=origin,
        history=history,
        current_tags={origin.tag},
    )
    assert state.anchor_park_id is None
    assert state.anchor_timezone is None


def test_tracker_projection_keeps_status_event_identity_and_previous_status():
    projected = tracker_client._project_issue_status_history(
        [
            {
                "id": "event-1",
                "updatedAt": "2026-09-25T07:00:00Z",
                "fields": [
                    {
                        "field": {"id": "status"},
                        "from": {"key": "new", "display": "Новая"},
                        "to": {"key": "queued", "display": "В очереди"},
                    }
                ],
            }
        ]
    )
    assert projected[0]["id"] == "event-1"
    assert projected[0]["fields"][0]["from"]["key"] == "new"


def test_closed_history_search_is_park_scoped():
    query = tracker_client.build_closed_blockers_query(
        "ROBOPARK",
        "Alpha",
        priority="blocker",
        since=datetime(2026, 8, 25, tzinfo=UTC),
        until=datetime(2026, 9, 25, tzinfo=UTC),
    )
    assert "Queue: ROBOPARK" in query
    assert "Tags: Alpha" in query
    assert "Priority: blocker" in query
    assert "Status: closed" in query
    assert "Updated:" in query
    assert "Updated: >= 2026-08-24" in query
    assert "Updated: < 2026-09-27" in query
    assert '"Sort By": Updated DESC' in query


def test_status_event_identity_is_bounded_without_losing_distinct_source_events(
    db_session,
    seed_park_with_tracker,
):
    at = datetime(2026, 9, 25, 7, tzinfo=UTC)
    history = [
        {**change(at, "queued", "В очереди"), "id": "a" * 300},
        {**change(at, "queued", "В очереди"), "id": "b" * 300},
    ]
    tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-1",
        park=seed_park_with_tracker,
        history=history,
    )
    keys = db_session.scalars(select(TrackerIssueStatusEvent.event_key)).all()
    assert len(keys) == len(set(keys)) == 2
    assert all(len(key) <= 160 for key in keys)


def test_multiple_status_changes_in_one_changelog_record_keep_order_and_identity(
    db_session,
    seed_park_with_tracker,
):
    at = datetime(2026, 9, 25, 7, tzinfo=UTC)
    history = [
        {
            "id": "tracker-change-1",
            "updatedAt": at.isoformat(),
            "fields": [
                {"field": {"id": "status"}, "to": {"key": "queued", "display": "В очереди"}},
                {"field": {"id": "status"}, "to": {"key": "closed", "display": "Закрыт"}},
            ],
        }
    ]
    projected = tracker_client._project_issue_status_history(history)

    for _ in range(2):
        state = tracker_history.ingest_status_history(
            db_session,
            issue_key="RP-MULTI",
            park=seed_park_with_tracker,
            history=projected,
        )

    events = db_session.scalars(select(TrackerIssueStatusEvent)).all()
    assert len(events) == 2
    assert state.first_queued_at.replace(tzinfo=UTC) == at
    assert state.latest_status_key == "closed"
    assert state.terminal_at.replace(tzinfo=UTC) == at


def test_same_timestamp_status_records_do_not_invent_latest_closure(
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.services.analytics import verified_closures

    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    tied = queued + timedelta(hours=1)
    history = [
        {**change(queued, "queued", "В очереди"), "id": "queued"},
        {**change(tied, "inProgress", "В работе"), "id": "a-reopened"},
        {**change(tied, "closed", "Закрыт"), "id": "z-closed"},
    ]
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-TIED",
        park=seed_park_with_tracker,
        current_tags={seed_park_with_tracker.tag},
        history=history,
    )
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.terminal_at is None
    assert (
        verified_closures(
            db_session,
            park_id=seed_park_with_tracker.id,
            start=queued,
            end=tied + timedelta(hours=1),
            scoped=False,
        ).count
        == 0
    )


def test_empty_verified_history_keeps_queue_unknown(db_session, seed_park_with_tracker):
    tracker_history.ingest_status_history(
        db_session, issue_key="RP-2", park=seed_park_with_tracker, history=[]
    )
    state = db_session.get(TrackerIssueHistoryState, "RP-2")
    assert state.history_state == "no_queue"
    assert state.first_queued_at is None
    assert state.terminal_at is None


def test_analytics_uses_only_verified_queue_anchor(db_session, seed_park_with_tracker):
    from robopark_api.services.analytics import build_analytics
    from robopark_api.services.analytics_history import record_observation

    park = seed_park_with_tracker
    now = datetime(2026, 9, 25, 16, 30, tzinfo=UTC)
    observed = now - timedelta(hours=1, minutes=30)
    queued = observed - timedelta(hours=6)
    tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-1",
        park=park,
        history=[change(queued, "queued", "В очереди")],
    )
    record_observation(
        db_session,
        park=park,
        issues=[
            {
                "key": "RP-1",
                "queue": park.tracker_queue,
                "tags": [park.tag],
                "status_key": "queued",
            },
            {
                "key": "RP-2",
                "queue": park.tracker_queue,
                "tags": [park.tag],
                "status_key": "queued",
            },
        ],
        observed_at=observed,
        target_hours=5,
    )
    result = build_analytics(db_session, park_id=park.id, days=1, bucket="2h", now=now)
    assert result.sla_trend.value == 100
    assert result.sla_trend.sample_count == 1
    assert result.sla_trend.complete is False
    assert "queue_history_unavailable" in result.warnings
    bands = {item.key: item for item in result.backlog_age_bands}
    assert bands["under_24h"].value == 1
    assert bands["unknown"].value == 1


def test_history_scan_reuses_park_list_and_limits_changelogs_globally(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    calls = []
    list_calls = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(
        tracker_client,
        "fetch_park_blockers",
        lambda **kw: (
            list_calls.append(kw)
            or [
                {
                    "key": f"RP-{index}",
                    "queue": park.tracker_queue,
                    "tags": [park.tag],
                    "status_key": "queued",
                }
                for index in range(3)
            ]
        ),
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda *, token, key, issue: (
            calls.append(key)
            or [change(datetime(2026, 9, 25, 9, tzinfo=UTC), "queued", "В очереди")]
        ),
    )
    assert scan_all_parks_once(db_session, now=datetime(2026, 9, 25, 12, tzinfo=UTC)) == 1
    assert len(list_calls) == 1
    assert calls == ["RP-0", "RP-1"]
    assert scan_all_parks_once(db_session, now=datetime(2026, 9, 25, 14, tzinfo=UTC)) == 1
    assert calls == ["RP-0", "RP-1", "RP-2"]


def test_history_scan_discovers_closed_tickets_missing_from_active_snapshots_with_bounded_pages(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.models import TrackerHistoryBackfillCursor
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    at = datetime(2026, 9, 24, 10, tzinfo=UTC)
    searches = []
    histories = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: [])

    def page(**kwargs):
        searches.append(kwargs)
        keys = range(50) if kwargs["page"] == 1 else range(50, 51)
        return [
            {
                "key": f"RP-{index:02}",
                "queue": park.tracker_queue,
                "tags": ["Other"] if index == 48 else [park.tag],
                "status_key": "cancelled" if index == 49 else "closed",
                "updated": at.isoformat(),
            }
            for index in keys
        ]

    monkeypatch.setattr(tracker_client, "search_closed_history_page", page)
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kw: {"key": kw["key"], "queue": park.tracker_queue, "tags": [park.tag]},
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: (
            histories.append(kw["key"])
            or [
                change(at - timedelta(hours=2), "queued", "В очереди"),
                change(at, "closed", "Закрыт"),
            ]
        ),
    )
    first_scan = datetime(2026, 9, 25, 12, tzinfo=UTC)
    assert scan_all_parks_once(db_session, now=first_scan) == 1
    cursor = db_session.get(TrackerHistoryBackfillCursor, park.id)
    assert cursor.next_page == 2
    assert len(searches) == 1
    assert len(histories) == 2
    assert db_session.get(TrackerIssueHistoryState, histories[0]).terminal_at is not None
    assert db_session.get(TrackerIssueHistoryState, "RP-48") is None
    assert db_session.get(TrackerIssueHistoryState, "RP-49") is None
    from robopark_api.services.analytics import build_analytics

    assert (
        build_analytics(
            db_session,
            park_id=park.id,
            days=7,
            bucket="1d",
            now=first_scan,
        ).verified_closures.count
        == 2
    )
    assert scan_all_parks_once(db_session, now=first_scan + timedelta(hours=2)) == 1
    assert [call["page"] for call in searches] == [1, 2]
    assert len(histories) == 4
    assert db_session.get(TrackerHistoryBackfillCursor, park.id).next_page == 1


def test_closed_history_search_failure_keeps_cursor_and_active_scan_available(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.models import TrackerHistoryBackfillCursor
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    calls = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(
        tracker_client,
        "fetch_park_blockers",
        lambda **kw: [
            {
                "key": "RP-LIVE",
                "queue": park.tracker_queue,
                "tags": [park.tag],
                "status_key": "queued",
            }
        ],
    )
    monkeypatch.setattr(
        tracker_client,
        "search_closed_history_page",
        lambda **kw: (
            calls.append(kw["page"])
            or (_ for _ in ()).throw(tracker_client.TrackerError("timeout"))
        ),
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: [change(datetime(2026, 9, 25, 10, tzinfo=UTC), "queued", "В очереди")],
    )
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    assert scan_all_parks_once(db_session, now=now) == 1
    assert db_session.get(TrackerIssueHistoryState, "RP-LIVE").first_queued_at is not None
    assert db_session.get(TrackerHistoryBackfillCursor, park.id).next_page == 1
    from robopark_api.services.analytics import build_analytics

    assert (
        "closed_history_search_failed"
        in build_analytics(
            db_session,
            park_id=park.id,
            days=1,
            bucket="2h",
            now=now + timedelta(hours=1),
        ).warnings
    )
    assert scan_all_parks_once(db_session, now=now + timedelta(hours=2)) == 1
    assert calls == [1, 1]


def test_closed_history_page_with_missing_updated_time_does_not_advance_cursor(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.models import TrackerHistoryBackfillCursor
    from robopark_api.services.analytics import build_analytics
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    searched_pages = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: [])
    monkeypatch.setattr(
        tracker_client,
        "search_closed_history_page",
        lambda **kw: (
            searched_pages.append(kw["page"])
            or [
                {
                    "key": "RP-INCOMPLETE",
                    "queue": park.tracker_queue,
                    "tags": [park.tag],
                    "status_key": "closed",
                }
            ]
        ),
    )
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    assert scan_all_parks_once(db_session, now=now) == 1
    cursor = db_session.get(TrackerHistoryBackfillCursor, park.id)
    assert cursor.next_page == 1
    assert cursor.search_failed is True
    assert (
        "closed_history_search_failed"
        in build_analytics(
            db_session, park_id=park.id, days=1, bucket="2h", now=now + timedelta(hours=1)
        ).warnings
    )
    assert scan_all_parks_once(db_session, now=now + timedelta(hours=2)) == 1
    assert searched_pages == [1, 1]


def test_closed_history_discovery_rotates_parks_with_one_search_per_cycle(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics_history import scan_all_parks_once

    other = Park(
        name="South", tag="South", tracker_queue="ROBOPARK", is_active=True, feature_blockers=True
    )
    db_session.add(other)
    db_session.commit()
    searched = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: [])
    monkeypatch.setattr(
        tracker_client,
        "search_closed_history_page",
        lambda **kw: searched.append(kw["query"]) or [],
    )
    first = datetime(2026, 9, 25, 12, tzinfo=UTC)
    assert scan_all_parks_once(db_session, now=first) == 2
    assert len(searched) == 1
    assert scan_all_parks_once(db_session, now=first + timedelta(hours=2)) == 2
    assert len(searched) == 2
    assert "Tags: Alpha" in searched[0]
    assert "Tags: South" in searched[1]


def test_closed_history_page_cap_is_reported_in_analytics(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.models import TrackerHistoryBackfillCursor
    from robopark_api.services.analytics import build_analytics
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    db_session.add(
        TrackerHistoryBackfillCursor(
            park_id=park.id,
            scan_since=now - timedelta(days=30),
            scan_until=now,
            next_page=200,
        )
    )
    db_session.commit()
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: [])
    monkeypatch.setattr(
        tracker_client,
        "search_closed_history_page",
        lambda **kw: [
            {
                "key": f"RP-{index}",
                "queue": park.tracker_queue,
                "tags": [park.tag],
                "status_key": "closed",
                "updated": now.isoformat(),
            }
            for index in range(50)
        ],
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: [
            change(now - timedelta(hours=2), "queued", "В очереди"),
            change(now - timedelta(hours=1), "closed", "Закрыт"),
        ],
    )
    assert scan_all_parks_once(db_session, now=now) == 1
    assert db_session.get(TrackerHistoryBackfillCursor, park.id).next_page == 1
    assert (
        "closed_history_page_cap"
        in build_analytics(
            db_session,
            park_id=park.id,
            days=1,
            bucket="2h",
            now=now + timedelta(hours=1),
        ).warnings
    )


def test_history_scan_reconciles_disappeared_open_ticket(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics import verified_closures
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    queued = datetime(2026, 9, 25, 9, tzinfo=UTC)
    listed = [
        {"key": "RP-1", "queue": park.tracker_queue, "tags": [park.tag], "status_key": "queued"}
    ]
    histories = [
        [change(queued, "queued", "В очереди")],
        [
            change(queued, "queued", "В очереди"),
            change(queued + timedelta(hours=3), "closed", "Закрыт"),
        ],
    ]
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: listed)
    fetched = []
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kw: (
            fetched.append(kw["key"])
            or {
                "key": kw["key"],
                "queue": park.tracker_queue,
                "tags": [park.tag],
                "status_key": "closed",
            }
        ),
    )
    monkeypatch.setattr(tracker_client, "get_issue_status_history", lambda **kw: histories.pop(0))
    assert scan_all_parks_once(db_session, now=datetime(2026, 9, 25, 12, tzinfo=UTC)) == 1
    listed.clear()
    assert scan_all_parks_once(db_session, now=datetime(2026, 9, 25, 14, tzinfo=UTC)) == 1
    state = db_session.get(TrackerIssueHistoryState, "RP-1")
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.terminal_at.replace(tzinfo=UTC) == queued + timedelta(hours=3)
    assert fetched == ["RP-1"]
    assert (
        verified_closures(
            db_session,
            park_id=park.id,
            start=queued,
            end=queued + timedelta(hours=4),
            scoped=False,
        ).count
        == 1
    )


def test_missing_ticket_lookup_preserves_verified_history_for_retry(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    queued = datetime(2026, 9, 25, 9, tzinfo=UTC)
    listed = [
        {
            "key": "RP-LOOKUP",
            "queue": park.tracker_queue,
            "tags": [park.tag],
            "status_key": "queued",
        }
    ]
    history_reads = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: listed)
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: history_reads.append(kw["key"]) or [change(queued, "queued", "В очереди")],
    )
    scan_all_parks_once(db_session, now=queued + timedelta(hours=1))
    state = db_session.get(TrackerIssueHistoryState, "RP-LOOKUP")
    assert state.anchor_park_id == park.id

    listed.clear()
    monkeypatch.setattr(tracker_client, "get_issue", lambda **kw: None)
    scan_all_parks_once(db_session, now=queued + timedelta(hours=3))
    db_session.refresh(state)
    assert state.history_state == "retry"
    assert state.anchor_park_id == park.id
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert history_reads == ["RP-LOOKUP"]


def test_disappeared_open_ticket_does_not_consume_changelog_budget_every_cycle(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    queued = now - timedelta(hours=8)
    tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-OLD",
        park=park,
        history=[change(queued, "queued", "В очереди")],
        checked_at=now - timedelta(hours=7),
    )
    calls = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: [])
    monkeypatch.setattr(tracker_client, "search_closed_history_page", lambda **kw: [])
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kw: {"key": kw["key"], "queue": park.tracker_queue, "tags": [park.tag]},
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: calls.append(kw["key"]) or [change(queued, "queued", "В очереди")],
    )
    assert scan_all_parks_once(db_session, now=now) == 1
    assert calls == ["RP-OLD"]
    assert scan_all_parks_once(db_session, now=now + timedelta(hours=2)) == 1
    assert calls == ["RP-OLD"]
    assert scan_all_parks_once(db_session, now=now + timedelta(hours=6)) == 1
    assert calls == ["RP-OLD", "RP-OLD"]


def test_newly_disappeared_ticket_precedes_large_old_recheck_backlog(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics_history import record_observation, scan_all_parks_once

    park = seed_park_with_tracker
    now = datetime(2026, 9, 25, 14, tzinfo=UTC)
    for index in range(201):
        db_session.add(
            TrackerIssueHistoryState(
                issue_key=f"RP-OLD-{index:03d}",
                observed_park_id=park.id,
                history_state="complete",
                history_checked_at=now - timedelta(days=1),
            )
        )
    db_session.add(
        TrackerIssueHistoryState(
            issue_key="RP-NEW",
            observed_park_id=park.id,
            history_state="complete",
            history_checked_at=now - timedelta(hours=1),
        )
    )
    db_session.commit()
    record_observation(
        db_session,
        park=park,
        issues=[
            {
                "key": "RP-NEW",
                "queue": park.tracker_queue,
                "tags": [park.tag],
                "status_key": "queued",
            }
        ],
        observed_at=now - timedelta(hours=2),
        target_hours=5,
    )
    calls = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: [])
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kw: {"key": kw["key"], "queue": park.tracker_queue, "tags": [park.tag]},
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: (
            calls.append(kw["key"]) or [change(now - timedelta(hours=3), "queued", "В очереди")]
        ),
    )
    assert scan_all_parks_once(db_session, now=now) == 1
    assert "RP-NEW" in calls


def test_scan_recovers_queue_of_ticket_closed_before_its_first_history_read(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    queued = datetime(2026, 9, 25, 9, tzinfo=UTC)
    listed = [
        {
            "key": f"RP-{index}",
            "queue": park.tracker_queue,
            "tags": [park.tag],
            "status_key": "queued",
        }
        for index in range(3)
    ]
    calls = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **kw: listed)
    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **kw: {"key": kw["key"], "queue": park.tracker_queue, "tags": [park.tag]},
    )

    def history(**kw):
        calls.append(kw["key"])
        result = [change(queued, "queued", "В очереди")]
        if kw["key"] == "RP-2":
            result.append(change(queued + timedelta(hours=2), "closed", "Закрыт"))
        return result

    monkeypatch.setattr(tracker_client, "get_issue_status_history", history)
    scan_all_parks_once(db_session, now=datetime(2026, 9, 25, 12, tzinfo=UTC))
    assert calls == ["RP-0", "RP-1"]
    listed.clear()
    scan_all_parks_once(db_session, now=datetime(2026, 9, 25, 14, tzinfo=UTC))
    assert "RP-2" in calls
    state = db_session.get(TrackerIssueHistoryState, "RP-2")
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert state.terminal_at.replace(tzinfo=UTC) == queued + timedelta(hours=2)


def test_history_scan_gives_two_parks_one_read_each(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.services.analytics_history import scan_all_parks_once

    second = Park(
        name="South", tag="South", tracker_queue="ROBOPARK", is_active=True, feature_blockers=True
    )
    db_session.add(second)
    db_session.commit()
    keys = []
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(
        tracker_client,
        "fetch_park_blockers",
        lambda **kw: [
            {
                "key": f"{kw['park_tag']}-{index}",
                "queue": kw["queue"],
                "tags": [kw["park_tag"]],
                "status_key": "queued",
            }
            for index in range(3)
        ],
    )
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: (
            keys.append(kw["key"])
            or [change(datetime(2026, 9, 25, 9, tzinfo=UTC), "queued", "В очереди")]
        ),
    )
    assert scan_all_parks_once(db_session, now=datetime(2026, 9, 25, 12, tzinfo=UTC)) == 2
    assert len(keys) == 2
    assert {key.split("-")[0] for key in keys} == {"Alpha", "South"}


def test_history_migration_upgrades_from_park_timezone(sqlite_database_url, monkeypatch):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    command.upgrade(config, "0051_park_timezone")
    command.upgrade(config, "head")
    inspector = inspect(create_engine(sqlite_database_url))
    tables = set(inspector.get_table_names())
    assert {
        "tracker_issue_history_state",
        "tracker_issue_status_events",
        "tracker_history_backfill_cursors",
    } <= tables
    assert any(
        fk["constrained_columns"] == ["issue_key"]
        and fk["referred_table"] == "tracker_issue_history_state"
        for fk in inspector.get_foreign_keys("tracker_issue_status_events")
    )
    assert any(
        fk["constrained_columns"] == ["park_id"] and fk["referred_table"] == "parks"
        for fk in inspector.get_foreign_keys("tracker_issue_status_events")
    )


def test_verified_projection_supplies_overview_sla_without_tracker_history_read(
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.services.operations import calculate_sla

    park = seed_park_with_tracker
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-1",
        park=park,
        history=[change(queued, "queued", "В очереди")],
    )
    items = tracker_history.attach_verified_history(
        db_session,
        [
            {
                "key": "RP-1",
                "summary": "Repair",
                "status": "Diagnostics",
                "status_key": "diagnostics",
            },
            {
                "key": "RP-2",
                "summary": "Repair",
                "status": "Diagnostics",
                "status_key": "diagnostics",
            },
        ],
    )
    assert items[0]["queued_at"] == queued.isoformat().replace("+00:00", "Z")
    result = calculate_sla(
        items,
        target_hours=5,
        now=queued + timedelta(hours=6),
        timezone=park.timezone,
    )
    assert result.evaluated_count == 1
    assert result.unknown_count == 1
    assert result.overdue_count == 1


def test_work_detail_persists_history_already_fetched_for_its_sla(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
):
    from robopark_api.routers.tracker_read import _work_issue_sla

    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    calls = []
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: calls.append(kw["key"]) or [change(queued, "queued", "В очереди")],
    )
    issue = _work_issue_sla(
        token="test-token",
        issue={"key": "RP-1", "status_key": "diagnostics", "tags": [seed_park_with_tracker.tag]},
        db=db_session,
        park=seed_park_with_tracker,
    )
    assert calls == ["RP-1"]
    assert issue["queued_at"] == queued.isoformat().replace("+00:00", "Z")
    assert (
        db_session.get(TrackerIssueHistoryState, "RP-1").first_queued_at.replace(tzinfo=UTC)
        == queued
    )


@pytest.mark.parametrize(
    "error,state",
    [
        ("Tracker HTTP 403 Forbidden", "forbidden"),
        ("Tracker timeout", "retry"),
    ],
)
def test_history_fetch_error_remains_visible_without_inventing_sla(
    db_session,
    seed_park_with_tracker,
    monkeypatch,
    error,
    state,
):
    from robopark_api.services.analytics_history import scan_all_parks_once

    park = seed_park_with_tracker
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda db: "test-token")
    monkeypatch.setattr(
        tracker_client,
        "fetch_park_blockers",
        lambda **kw: [
            {"key": "RP-1", "queue": park.tracker_queue, "tags": [park.tag], "status_key": "queued"}
        ],
    )

    def fail_history(**kw):
        raise tracker_client.TrackerError(error)

    monkeypatch.setattr(tracker_client, "get_issue_status_history", fail_history)
    scan_all_parks_once(db_session, now=datetime(2026, 9, 25, 12, tzinfo=UTC))
    row = db_session.get(TrackerIssueHistoryState, "RP-1")
    assert row.history_state == state
    assert row.first_queued_at is None
    from robopark_api.services.analytics import build_analytics

    analytics = build_analytics(
        db_session,
        park_id=park.id,
        days=1,
        bucket="2h",
        now=datetime(2026, 9, 25, 14, 30, tzinfo=UTC),
    )
    assert (
        "history_access_denied" if state == "forbidden" else "history_source_unavailable"
    ) in analytics.warnings


def test_temporary_history_failure_does_not_erase_previously_verified_queue_start(
    db_session,
    seed_park_with_tracker,
):
    from robopark_api.services.analytics import build_analytics
    from robopark_api.services.analytics_history import record_observation

    park = seed_park_with_tracker
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-1",
        park=park,
        history=[change(queued, "queued", "В очереди")],
    )
    state = db_session.get(TrackerIssueHistoryState, "RP-1")
    state.history_state = "retry"
    db_session.commit()
    assert (
        tracker_history.attach_verified_history(db_session, [{"key": "RP-1"}])[0]["queued_at"]
        == "2026-09-25T07:00:00Z"
    )
    record_observation(
        db_session,
        park=park,
        issues=[
            {"key": "RP-1", "queue": park.tracker_queue, "tags": [park.tag], "status_key": "queued"}
        ],
        observed_at=datetime(2026, 9, 25, 15, tzinfo=UTC),
        target_hours=5,
    )
    result = build_analytics(
        db_session,
        park_id=park.id,
        days=1,
        bucket="2h",
        now=datetime(2026, 9, 25, 16, 30, tzinfo=UTC),
    )
    assert result.sla_trend.value == 100
    assert "history_source_unavailable" in result.warnings


def test_case_variant_park_tag_preserves_queue_anchor(db_session, seed_park_with_tracker):
    park = seed_park_with_tracker
    park.tag = "АрмаМск"
    db_session.commit()
    queued = datetime(2026, 9, 25, 7, tzinfo=UTC)
    state = tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-CASE",
        park=park,
        history=[change(queued, "queued", "В очереди")],
        current_tags={"АрмаМСК"},
    )
    assert state.anchor_park_id == park.id
    assert state.anchor_timezone == park.timezone


def test_pending_sla_batch_fills_shared_anchors_without_opening_details(
    db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import tracker_cache

    park = seed_park_with_tracker
    queued = datetime(2026, 9, 30, 6, tzinfo=UTC)
    issues = [
        {"key": f"RP-BATCH-{i}", "queue": park.tracker_queue, "tags": [park.tag.upper()]}
        for i in range(4)
    ]
    tracker_history.register_pending_history(db_session, issues, [park])
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _: "fixture-token")
    monkeypatch.setattr(
        tracker_cache,
        "get_issue",
        lambda **kw: next(item for item in issues if item["key"] == kw["key"]),
    )
    calls = []

    def history(**kw):
        calls.append(kw["key"])
        return [change(queued, "queued", "В очереди")]

    monkeypatch.setattr(tracker_client, "get_issue_status_history", history)
    assert tracker_history.drain_pending_history(db_session, now=queued, limit=20) == 2
    assert len(calls) == 2
    assert tracker_history.drain_pending_history(db_session, now=queued) == 2
    assert len(set(calls)) == 4
    assert tracker_history.drain_pending_history(db_session, now=queued) == 0
    shared = tracker_history.attach_verified_history(db_session, issues)
    assert {item["queued_at"] for item in shared} == {"2026-09-30T06:00:00Z"}
    assert {item["sla_anchor_timezone"] for item in shared} == {"Europe/Moscow"}


def test_pending_sla_forbidden_backoff_does_not_starve_other_tasks(
    db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import tracker_cache

    park = seed_park_with_tracker
    now = datetime(2026, 9, 30, 6, tzinfo=UTC)
    denied = TrackerIssueHistoryState(
        issue_key="RP-A",
        observed_park_id=park.id,
        history_state="forbidden",
        history_checked_at=now,
    )
    db_session.add(denied)
    db_session.commit()
    issue = {"key": "RP-B", "queue": park.tracker_queue, "tags": [park.tag]}
    tracker_history.register_pending_history(db_session, [issue], [park])
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _: "fixture-token")
    monkeypatch.setattr(tracker_cache, "get_issue", lambda **kw: issue)
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: [change(now, "queued", "В очереди")],
    )
    assert tracker_history.drain_pending_history(db_session, now=now) == 1
    assert db_session.get(TrackerIssueHistoryState, "RP-A").first_queued_at is None
    assert db_session.get(TrackerIssueHistoryState, "RP-B").first_queued_at is not None


@pytest.mark.parametrize("status", ["queued", "inProgress"])
def test_new_queue_observation_reactivates_prequeue_history(
    db_session, seed_park_with_tracker, status
):
    park = seed_park_with_tracker
    checked = datetime(2026, 9, 30, 6, tzinfo=UTC)
    row = TrackerIssueHistoryState(
        issue_key="RP-PREQUEUE",
        observed_park_id=park.id,
        history_state="no_queue",
        history_checked_at=checked,
    )
    db_session.add(row)
    db_session.commit()
    tracker_history.register_pending_history(
        db_session,
        [
            {
                "key": row.issue_key,
                "queue": park.tracker_queue,
                "tags": [park.tag],
                "status_key": status,
                "updated": (checked + timedelta(minutes=1)).isoformat(),
            }
        ],
        [park],
    )
    db_session.refresh(row)
    assert row.history_state == "unknown"
    assert row.first_queued_at is None


def test_ingestion_survives_state_created_between_lookup_and_insert(
    db_session, seed_park_with_tracker, monkeypatch
):
    park = seed_park_with_tracker
    queued = datetime(2026, 9, 30, 6, tzinfo=UTC)
    db_session.add(
        TrackerIssueHistoryState(
            issue_key="RP-RACE", observed_park_id=park.id, history_state="unknown"
        )
    )
    db_session.commit()
    original_get = db_session.get
    monkeypatch.setattr(
        db_session,
        "get",
        lambda model, key, **kw: (
            None
            if model is TrackerIssueHistoryState and key == "RP-RACE"
            else original_get(model, key, **kw)
        ),
    )
    tracker_history.ingest_status_history(
        db_session, issue_key="RP-RACE", park=park, history=[change(queued, "queued", "В очереди")]
    )
    state = db_session.scalar(
        select(TrackerIssueHistoryState).where(TrackerIssueHistoryState.issue_key == "RP-RACE")
    )
    assert state.first_queued_at.replace(tzinfo=UTC) == queued
    assert len(db_session.scalars(select(TrackerIssueStatusEvent)).all()) == 1


def test_detail_reuses_shared_verified_anchor_without_tracker_read(
    db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.routers.tracker_read import _work_issue_sla

    park = seed_park_with_tracker
    queued = datetime(2026, 9, 30, 6, tzinfo=UTC)
    tracker_history.ingest_status_history(
        db_session,
        issue_key="RP-CACHED",
        park=park,
        history=[change(queued, "queued", "В очереди")],
    )

    def unexpected_history(**kw):
        pytest.fail("A shared verified SLA anchor must not need a new Tracker read")

    monkeypatch.setattr(tracker_client, "get_issue_status_history", unexpected_history)
    result = _work_issue_sla(
        token="fixture-token",
        db=db_session,
        park=park,
        issue={"key": "RP-CACHED", "queue": park.tracker_queue, "tags": [park.tag]},
    )
    assert result["queued_at"] == "2026-09-30T06:00:00Z"


def test_prequeue_result_is_rechecked_after_issue_and_history_caches_expire(
    db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import tracker_cache

    park = seed_park_with_tracker
    now = datetime(2026, 9, 30, 6, tzinfo=UTC)
    issue = {
        "key": "RP-STALE",
        "queue": park.tracker_queue,
        "tags": [park.tag],
        "status_key": "inProgress",
    }
    tracker_history.register_pending_history(db_session, [issue], [park])
    monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _: "fixture-token")
    monkeypatch.setattr(tracker_cache, "get_issue", lambda **kw: issue)
    monkeypatch.setattr(tracker_client, "get_issue_status_history", lambda **kw: [])
    assert tracker_history.drain_pending_history(db_session, now=now) == 1
    assert db_session.get(TrackerIssueHistoryState, issue["key"]).history_state == "no_queue"
    monkeypatch.setattr(
        tracker_client,
        "get_issue_status_history",
        lambda **kw: [change(now, "queued", "В очереди")],
    )
    assert tracker_history.drain_pending_history(db_session, now=now + timedelta(seconds=30)) == 0
    assert tracker_history.drain_pending_history(db_session, now=now + timedelta(minutes=2)) == 1
    assert (
        db_session.get(TrackerIssueHistoryState, issue["key"]).first_queued_at.replace(tzinfo=UTC)
        == now
    )
