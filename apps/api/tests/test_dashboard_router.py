import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from conftest import login_as
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services.blocker_history import upsert_bucket
from robopark_api.services.tracker_client import issue_to_dict

FIXTURES = Path(__file__).parent / "fixtures"


def seed_dashboard_park(db_session, *, username="op-dash", role="operator"):
    park = Park(
        name="Alpha",
        tag="Alpha",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_blockers=True,
        feature_reports=True,
    )
    db_session.add(park)
    db_session.flush()
    user = User(
        username=username,
        password_hash=hash_password("secret"),
        role=role,
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=park.id))
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(park)
    return user, park


def test_dashboard_summary_operator_ok(client, db_session, seed_royal):
    _, park = seed_dashboard_park(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-dash", "secret")

    fake_metrics = {
        "open_blockers": 3,
        "backlog": 1,
        "in_transit": 2,
        "queued": 4,
        "waiting_team": 0,
        "waiting_parts": 0,
        "arrived": 5,
        "done": 6,
    }
    issues = [
        issue_to_dict(item)
        for item in json.loads((FIXTURES / "tracker_issues.json").read_text())
    ]
    with (
        patch(
            "robopark_api.services.tracker_metrics.collect_park_metrics",
            return_value=fake_metrics,
        ),
        patch(
            "robopark_api.services.tracker_client.fetch_park_blockers",
            return_value=issues,
        ),
    ):
        response = client.get(f"/dashboard/summary?park_id={park.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["park_id"] == park.id
    assert body["arrived"] == 5
    assert body["done"] == 6
    assert body["queued"] == 4
    assert body["in_transit"] == 2
    assert len(body["moving"]) == 1
    assert body["moving"][0]["key"] == "ROBOPARK-2"


def test_dashboard_summary_mechanic_other_park_403(client, db_session, seed_mechanic):
    foreign = Park(name="Foreign", tag="Foreign", is_active=True, tracker_queue="ROBOPARK")
    db_session.add(foreign)
    db_session.commit()
    login_as(client, "mech1", "secret")
    response = client.get(f"/dashboard/summary?park_id={foreign.id}")
    assert response.status_code == 403


def test_dashboard_summary_not_found(client, db_session, seed_mechanic):
    login_as(client, "mech1", "secret")
    response = client.get("/dashboard/summary?park_id=99999")
    assert response.status_code == 404


def test_dashboard_history_shape(client, db_session, seed_royal):
    _, park = seed_dashboard_park(db_session)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    upsert_bucket(
        db_session,
        park_id=park.id,
        bucket_start=now - timedelta(hours=2),
        arrived_count=3,
        departed_count=1,
    )
    upsert_bucket(
        db_session,
        park_id=park.id,
        bucket_start=now,
        arrived_count=2,
        departed_count=4,
    )
    login_as(client, "op-dash", "secret")
    response = client.get(f"/dashboard/history?park_id={park.id}&days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["park_id"] == park.id
    assert len(body["points"]) == 2
    assert set(body["points"][0]) == {
        "bucket_start",
        "arrived_count",
        "departed_count",
    }
    assert body["points"][0]["arrived_count"] == 3
    assert body["points"][1]["departed_count"] == 4


def test_dashboard_history_mechanic_other_park_403(client, db_session, seed_mechanic):
    foreign = Park(name="Foreign", tag="Foreign", is_active=True)
    db_session.add(foreign)
    db_session.commit()
    login_as(client, "mech1", "secret")
    response = client.get(f"/dashboard/history?park_id={foreign.id}")
    assert response.status_code == 403


def test_dashboard_history_admin_any_park(client, db_session, seed_royal):
    park = Park(name="Solo", tag="Solo", is_active=True)
    db_session.add(park)
    db_session.commit()
    login_as(client, "royal", "secret")
    response = client.get(f"/dashboard/history?park_id={park.id}")
    assert response.status_code == 200
    assert response.json()["park_id"] == park.id
