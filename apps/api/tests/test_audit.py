"""Audit trail for authentication and Tracker write actions."""

import pytest

from conftest import login_as
from robopark_api.models import AuditLog
from robopark_api.services import audit, platform_settings
from robopark_api.services.login_throttle import reset_throttles


@pytest.fixture(autouse=True)
def clean_throttles():
    reset_throttles()
    yield
    reset_throttles()


def _issue(**overrides) -> dict:
    issue = {
        "key": "ROBOPARK-1",
        "summary": "blocker [447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "resolution": "",
        "tags": ["Alpha"],
        "assignee": {"login": "mech1", "display": "Mechanic"},
    }
    issue.update(overrides)
    return issue


def _mock_tracker(monkeypatch, issue=None):
    from robopark_api.services import tracker_client

    captured: dict = {}

    def _add_comment(**kwargs):
        captured.update(kwargs)
        return {"id": "1", "text": kwargs.get("text", "")}

    monkeypatch.setattr(tracker_client, "get_issue", lambda **_k: issue or _issue())
    monkeypatch.setattr(tracker_client, "add_comment", _add_comment)
    monkeypatch.setattr(
        tracker_client,
        "list_transitions",
        lambda **_k: [{"id": "close", "display": "Закрыть"}],
    )
    monkeypatch.setattr(tracker_client, "transition_issue", lambda **_k: None)
    monkeypatch.setattr(tracker_client, "assign_issue", lambda **_k: None)
    return captured


# --- authentication -----------------------------------------------------


def test_successful_login_is_audited(client, db_session, seed_royal):
    login_as(client, "royal", "secret")

    entry = db_session.query(AuditLog).filter_by(action=audit.ACTION_LOGIN_SUCCESS).one()
    assert entry.actor_user_id == seed_royal.id
    assert entry.actor_username == "royal"
    assert entry.outcome == audit.OUTCOME_SUCCESS


def test_failed_login_is_audited(client, db_session, seed_royal):
    login_as(client, "royal", "wrong-password")

    entry = db_session.query(AuditLog).filter_by(action=audit.ACTION_LOGIN_FAILED).one()
    assert entry.actor_username == "royal"
    assert entry.outcome == audit.OUTCOME_FAILURE
    # The attempted password must never be stored.
    assert entry.detail is None or "wrong-password" not in entry.detail


def test_lockout_is_audited(client, db_session, seed_royal, test_settings, monkeypatch):
    monkeypatch.setattr(test_settings, "login_max_attempts", 2)
    for _ in range(3):
        login_as(client, "royal", "wrong")

    assert db_session.query(AuditLog).filter_by(action=audit.ACTION_LOGIN_BLOCKED).count() >= 1


# --- Tracker actions ----------------------------------------------------


def test_close_is_audited_with_actor(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    """The core gap: Tracker shows one service account for every user."""
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "t")
    _mock_tracker(monkeypatch)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-1",
        park_id=seed_park_with_tracker.id,
    )

    login_as(client, "mech1", "secret")
    assert client.post("/tracker/issues/ROBOPARK-1/close").status_code == 200

    entry = db_session.query(AuditLog).filter_by(action=audit.ACTION_TRACKER_CLOSE).one()
    assert entry.actor_user_id == seed_mechanic.id
    assert entry.actor_role == "mechanic"
    assert entry.target_id == "ROBOPARK-1"
    assert entry.park_id == seed_park_with_tracker.id


def test_comment_is_signed_and_audited(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "t")
    captured = _mock_tracker(monkeypatch)
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-1",
        park_id=seed_park_with_tracker.id,
    )

    login_as(client, "mech1", "secret")
    response = client.post("/tracker/issues/ROBOPARK-1/comment", json={"text": "проверил робота"})
    assert response.status_code == 200

    # The comment carries the real author, not just the service account.
    assert "проверил робота" in captured["text"]
    assert "mech1" in captured["text"]

    entry = db_session.query(AuditLog).filter_by(action=audit.ACTION_TRACKER_COMMENT).one()
    assert entry.actor_username == "mech1"
    assert entry.target_id == "ROBOPARK-1"


def test_denied_action_is_audited(client, db_session, seed_mechanic, monkeypatch):
    """A blocked attempt must leave a trace, not vanish silently."""
    from robopark_api.models import Park

    db_session.add(Park(name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK"))
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "t")
    _mock_tracker(monkeypatch, issue=_issue(tags=["Beta"]))

    login_as(client, "mech1", "secret")
    response = client.post("/tracker/issues/ROBOPARK-1/comment", json={"text": "hi"})
    assert response.status_code == 403

    entry = (
        db_session.query(AuditLog)
        .filter_by(outcome=audit.OUTCOME_DENIED, action=audit.ACTION_TRACKER_COMMENT)
        .one()
    )
    assert entry.actor_username == "mech1"


def test_audit_failure_does_not_break_the_action(db_session, seed_royal, monkeypatch):
    """Auditing is observational: it must never fail the business operation."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("audit storage down")

    monkeypatch.setattr(db_session, "add", _boom)
    audit.record(db_session, action="test.action", actor=seed_royal)


# --- admin API ----------------------------------------------------------


def test_admin_can_read_audit_log(client, db_session, seed_royal):
    login_as(client, "royal", "secret")
    response = client.get("/admin/audit")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert any(item["action"] == audit.ACTION_LOGIN_SUCCESS for item in body["items"])


def test_audit_log_is_admin_only(client, db_session, seed_mechanic):
    login_as(client, "mech1", "secret")
    assert client.get("/admin/audit").status_code == 403


def test_audit_filtering_and_paging(client, db_session, seed_royal):
    login_as(client, "royal", "secret")
    for _ in range(3):
        audit.record(db_session, action="test.filter", actor=seed_royal)

    filtered = client.get("/admin/audit?action=test.filter&limit=2").json()
    assert filtered["total"] == 3
    assert len(filtered["items"]) == 2
    assert filtered["has_more"] is True

    page2 = client.get("/admin/audit?action=test.filter&limit=2&offset=2").json()
    assert len(page2["items"]) == 1
    assert page2["has_more"] is False


def test_audit_entries_are_newest_first(client, db_session, seed_royal):
    login_as(client, "royal", "secret")
    audit.record(db_session, action="test.order", actor=seed_royal, detail="first")
    audit.record(db_session, action="test.order", actor=seed_royal, detail="second")

    items = client.get("/admin/audit?action=test.order").json()["items"]
    assert [item["detail"] for item in items] == ["second", "first"]
