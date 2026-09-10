from types import SimpleNamespace

from robopark_api.services import tracker_claims


def test_mechanic_claim_is_local_and_never_needs_tracker_login(
    db_session, seed_mechanic, seed_park_with_tracker
):
    seed_mechanic.tracker_login = None
    db_session.commit()

    claim = tracker_claims.claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-9",
        park_id=seed_park_with_tracker.id,
    )

    assert claim.owner_user_id == seed_mechanic.id
    assert tracker_claims.mechanic_owns_issue(db_session, seed_mechanic, {"key": "ROBOPARK-9"})
    assert tracker_claims.local_assignee(db_session, {"key": "ROBOPARK-9"}) == {
        "login": seed_mechanic.username,
        "display": seed_mechanic.username,
    }


def test_unclaimed_issue_is_readable_but_not_owned(db_session, seed_mechanic):
    issue = {"key": "ROBOPARK-10", "assignee": {"login": "someone.in.tracker"}}

    assert tracker_claims.mechanic_can_access_issue(db_session, seed_mechanic, issue)
    assert not tracker_claims.mechanic_owns_issue(db_session, seed_mechanic, issue)


def test_other_mechanic_can_open_and_take_over_claim(
    db_session, seed_mechanic, seed_park_with_tracker
):
    other = SimpleNamespace(id=seed_mechanic.id + 100, role="mechanic", username="other")
    tracker_claims.claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-11",
        park_id=seed_park_with_tracker.id,
    )

    assert tracker_claims.mechanic_can_access_issue(db_session, other, {"key": "ROBOPARK-11"})


def test_staff_can_transfer_and_release_a_local_claim(
    db_session, seed_mechanic, seed_royal, seed_park_with_tracker
):
    tracker_claims.claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-12",
        park_id=seed_park_with_tracker.id,
    )
    tracker_claims.claim_issue(
        db_session,
        actor=seed_royal,
        owner=seed_royal,
        issue_key="ROBOPARK-12",
        park_id=seed_park_with_tracker.id,
        replace=True,
    )

    assert tracker_claims.mechanic_owns_issue(db_session, seed_royal, {"key": "ROBOPARK-12"})
    tracker_claims.release_claim(db_session, "ROBOPARK-12")
    assert tracker_claims.local_assignee(db_session, {"key": "ROBOPARK-12"}) is None
