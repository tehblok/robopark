"""Shared-writer safety for analytics SLA history discovery."""

from sqlalchemy import func, select

from robopark_api.models import TrackerIssueHistoryState
from robopark_api.services import analytics_history


def test_establish_history_states_is_conflict_safe_and_preserves_projection(
    db_session, seed_park_with_tracker
):
    park = seed_park_with_tracker
    existing = TrackerIssueHistoryState(
        issue_key="RP-RACE",
        observed_park_id=park.id,
        history_state="complete",
        anchor_park_id=park.id,
        anchor_timezone=park.timezone,
    )
    db_session.add(existing)
    db_session.commit()

    states = analytics_history._establish_history_states(
        db_session,
        {"RP-RACE": park.id, "RP-NEW": park.id},
    )

    assert set(states) == {"RP-RACE", "RP-NEW"}
    assert states["RP-RACE"].history_state == "complete"
    assert states["RP-RACE"].anchor_park_id == park.id
    assert states["RP-RACE"].anchor_timezone == park.timezone
    assert states["RP-NEW"].history_state == "unknown"
    assert db_session.scalar(select(func.count()).select_from(TrackerIssueHistoryState)) == 2

    analytics_history._establish_history_states(db_session, {"RP-RACE": park.id})

    assert db_session.scalar(select(func.count()).select_from(TrackerIssueHistoryState)) == 2
