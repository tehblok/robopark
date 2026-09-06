"""Fail-closed scope checks for Tracker issue access.

These tests lock in the security contract: a non-admin user may only reach an
issue that *provably* belongs to one of their parks. Anything unverifiable —
missing queue, foreign park tag, no park assigned — must be denied.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, event

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.services.tracker_policy import (
    enforce_issue_scope,
    is_issue_in_scope,
)


def _issue(**overrides) -> dict:
    issue = {
        "key": "ROBOPARK-1",
        "summary": "blocker [447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "resolution": "",
        "tags": ["Alpha"],
    }
    issue.update(overrides)
    return issue


@pytest.fixture
def operator(db_session, seed_park_with_tracker):
    user = User(
        username="op_scope",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    return user


def test_own_park_tag_is_in_scope(db_session, operator):
    enforce_issue_scope(db_session, operator, _issue())
    assert is_issue_in_scope(db_session, operator, _issue()) is True


def test_foreign_park_tag_denied(db_session, operator):
    """An issue tagged with another park must never be reachable."""
    db_session.add(Park(name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK"))
    db_session.commit()

    issue = _issue(tags=["Beta"])
    assert is_issue_in_scope(db_session, operator, issue) is False
    with pytest.raises(HTTPException) as exc:
        enforce_issue_scope(db_session, operator, issue)
    assert exc.value.status_code == 403


def test_missing_queue_denied(db_session, operator):
    """Regression: an unparseable/empty queue used to skip the check entirely."""
    assert is_issue_in_scope(db_session, operator, _issue(queue="")) is False


def test_foreign_queue_denied(db_session, operator):
    assert is_issue_in_scope(db_session, operator, _issue(queue="OTHERQUEUE")) is False


def test_untagged_issue_follows_policy(db_session, operator):
    """No park tag: allowed only while the untagged policy is on."""
    issue = _issue(tags=[])
    assert is_issue_in_scope(db_session, operator, issue) is True

    platform_settings.set_bool_setting(
        db_session, platform_settings.TRACKER_OPERATOR_UNTAGGED_KEY, False
    )
    assert is_issue_in_scope(db_session, operator, issue) is False


def test_mechanic_denied_outside_own_park(db_session, seed_mechanic):
    """Regression: the old check was dead code and let mechanics touch any issue."""
    db_session.add(Park(name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK"))
    db_session.commit()

    assert is_issue_in_scope(db_session, seed_mechanic, _issue(tags=["Beta"])) is False
    # Mechanics never get the untagged escape hatch either.
    assert is_issue_in_scope(db_session, seed_mechanic, _issue(tags=[])) is False


def test_mechanic_second_park_tag_in_scope(db_session, seed_mechanic):
    extra = Park(name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK")
    db_session.add(extra)
    db_session.flush()
    db_session.add(UserPark(user_id=seed_mechanic.id, park_id=extra.id))
    db_session.commit()

    assert is_issue_in_scope(db_session, seed_mechanic, _issue(tags=["Beta"])) is True


def test_mechanic_without_park_denied(db_session):
    mechanic = User(
        username="mech_scope_nopark",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(mechanic)
    db_session.commit()

    assert is_issue_in_scope(db_session, mechanic, _issue()) is False


def test_admin_bypasses_scope(db_session, seed_royal):
    enforce_issue_scope(db_session, seed_royal, _issue(queue="ANY", tags=["Whatever"]))


def test_list_scope_queries_do_not_grow_per_issue(
    client, db_engine, db_session, operator, monkeypatch
):
    from robopark_api.services import tracker_cache

    db_session.add(Park(name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK"))
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    items = [_issue()]
    monkeypatch.setattr(tracker_cache, "search_issues", lambda **kwargs: items)
    assert login_as(client, "op_scope", "secret").status_code == 204
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", observe)
    try:
        assert client.get("/tracker/issues").status_code == 200
        one_issue_queries = len(statements)
        statements.clear()
        items[:] = [_issue(key=f"ROBOPARK-{i}") for i in range(200)] + [
            _issue(key="FOREIGN-1", tags=["Beta"])
        ]
        response = client.get("/tracker/issues?limit=200")
        assert response.status_code == 200
        assert response.json()["total"] == 200
        assert all(row["key"] != "FOREIGN-1" for row in response.json()["items"])
        assert len(statements) <= one_issue_queries + 2
    finally:
        event.remove(db_engine, "before_cursor_execute", observe)

    # Scope snapshots must never be shared between requests: removing a park
    # takes effect on the very next request, even for the same raw cache hit.
    db_session.execute(delete(UserPark).where(UserPark.user_id == operator.id))
    db_session.commit()
    assert client.get("/tracker/issues").status_code == 403


def test_mechanic_cannot_close_foreign_park_issue(client, db_session, seed_mechanic, monkeypatch):
    """End-to-end: closing an issue of another park must be rejected with 403."""
    from robopark_api.services import tracker_client

    db_session.add(Park(name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK"))
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    transition_called = False

    def _transition(**_kwargs):
        nonlocal transition_called
        transition_called = True

    monkeypatch.setattr(tracker_client, "get_issue", lambda **_k: _issue(tags=["Beta"]))
    monkeypatch.setattr(
        tracker_client, "list_transitions", lambda **_k: [{"id": "close", "display": "Закрыть"}]
    )
    monkeypatch.setattr(tracker_client, "transition_issue", _transition)

    login_as(client, "mech1", "secret")
    response = client.post("/tracker/issues/ROBOPARK-1/close")

    assert response.status_code == 403
    assert transition_called is False
