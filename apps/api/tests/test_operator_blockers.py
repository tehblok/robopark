import json
from pathlib import Path
from unittest.mock import patch

from conftest import login_as
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services.tracker_client import issue_to_dict

FIXTURES = Path(__file__).parent / "fixtures"


def seed_op_with_park(db_session, *, feature_blockers=True, queue="ROBOPARK"):
    park = Park(
        name="Alpha",
        tag="Alpha",
        is_active=True,
        tracker_queue=queue,
        feature_blockers=feature_blockers,
        feature_reports=True,
    )
    db_session.add(park)
    db_session.flush()
    op = User(
        username="op-block",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.flush()
    db_session.add(UserPark(user_id=op.id, park_id=park.id))
    db_session.commit()
    db_session.refresh(op)
    db_session.refresh(park)
    return op, park


def test_blockers_requires_token(client, db_session, seed_royal):
    _, park = seed_op_with_park(db_session)
    login_as(client, "op-block", "secret")
    r = client.get(f"/operator/blockers?park_id={park.id}")
    assert r.status_code == 503
    assert r.json()["detail"] == "tracker_token_not_configured"


def test_blockers_forbidden_other_park(client, db_session, seed_royal):
    _, _park = seed_op_with_park(db_session)
    other = Park(name="Other", tag="Other", is_active=True, tracker_queue="ROBOPARK")
    db_session.add(other)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-block", "secret")
    r = client.get(f"/operator/blockers?park_id={other.id}")
    assert r.status_code == 403


def test_blockers_mocked_ok(client, db_session, seed_royal):
    _, park = seed_op_with_park(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-block", "secret")
    issues = [
        issue_to_dict(i)
        for i in json.loads((FIXTURES / "tracker_issues.json").read_text())
    ]
    with patch("robopark_api.services.tracker_client._search", return_value=issues):
        r = client.get(f"/operator/blockers?park_id={park.id}&status=all")
    assert r.status_code == 200
    body = r.json()
    assert body["park_id"] == park.id
    assert body["park_tag"] == "Alpha"
    assert body["counts"]["all"] == 2
    assert len(body["items"]) == 2


def test_blockers_disabled(client, db_session, seed_royal):
    _, park = seed_op_with_park(db_session, feature_blockers=False)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-block", "secret")
    r = client.get(f"/operator/blockers?park_id={park.id}")
    assert r.status_code == 409
    assert r.json()["detail"] == "blockers_disabled_for_park"
