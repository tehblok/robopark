from types import SimpleNamespace

from robopark_api.services.tracker_claims import (
    assignee_login,
    mechanic_login,
    mechanic_owns_issue,
)


def test_mechanic_claim_uses_tracker_login_and_requires_matching_assignee():
    user = SimpleNamespace(role="mechanic", tracker_login="mech.login", username="mech1")
    assert mechanic_login(user) == "mech.login"
    assert assignee_login({"assignee": {"login": "mech.login"}}) == "mech.login"
    assert mechanic_owns_issue(user, {"assignee": {"login": "mech.login"}}) is True
    assert mechanic_owns_issue(user, {"assignee": {"login": "other"}}) is False
    assert mechanic_owns_issue(user, {}) is False


def test_non_mechanic_does_not_require_a_claim():
    user = SimpleNamespace(role="operator", tracker_login=None, username="operator")
    assert mechanic_owns_issue(user, {}) is True
