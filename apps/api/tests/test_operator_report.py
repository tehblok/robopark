from unittest.mock import patch

from conftest import login_as
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import tracker_metrics


def seed_op_report(db_session):
    tracker_metrics.clear_metrics_cache()
    good = Park(
        name="Alpha",
        tag="Alpha",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_reports=True,
    )
    disabled = Park(
        name="Beta",
        tag="Beta",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_reports=False,
    )
    db_session.add_all([good, disabled])
    db_session.flush()
    op = User(
        username="op-report",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.flush()
    db_session.add_all(
        [
            UserPark(user_id=op.id, park_id=good.id),
            UserPark(user_id=op.id, park_id=disabled.id),
        ]
    )
    db_session.commit()
    db_session.refresh(good)
    return op, good, disabled


def test_now_report_skips_disabled_and_aggregates(client, db_session, seed_royal):
    seed_op_report(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-report", "secret")

    fake_metrics = {
        "open_blockers": 3,
        "backlog": 1,
        "in_transit": 0,
        "queued": 2,
        "waiting_team": 0,
        "waiting_parts": 1,
        "arrived": 4,
        "done": 5,
    }
    with patch(
        "robopark_api.services.tracker_metrics.collect_park_metrics",
        return_value=fake_metrics,
    ):
        r = client.get("/operator/now-report")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "all"
    assert body["totals"]["blocker"] == 3
    assert body["totals"]["done"] == 5
    assert len(body["parks"]) == 1
    assert body["parks"][0]["park_tag"] == "Alpha"
    assert any(s["reason"] == "reports_disabled" for s in body["skipped_parks"])


def test_now_report_forbidden_foreign_park(client, db_session, seed_royal):
    seed_op_report(db_session)
    foreign = Park(name="Z", tag="Z", is_active=True, tracker_queue="ROBOPARK")
    db_session.add(foreign)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-report", "secret")
    r = client.get(f"/operator/now-report?park_id={foreign.id}")
    assert r.status_code == 403


def test_now_report_uses_cache(client, db_session, seed_royal):
    seed_op_report(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-report", "secret")
    with patch(
        "robopark_api.services.tracker_metrics.collect_park_metrics",
        return_value={
            "open_blockers": 1,
            "backlog": 0,
            "in_transit": 0,
            "queued": 0,
            "waiting_team": 0,
            "waiting_parts": 0,
            "arrived": 0,
            "done": 0,
        },
    ) as mocked:
        assert client.get("/operator/now-report").status_code == 200
        assert client.get("/operator/now-report").status_code == 200
        assert mocked.call_count == 1


def test_now_report_no_parks(client, db_session, seed_royal):
    op = User(
        username="op-none",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-none", "secret")
    r = client.get("/operator/now-report")
    assert r.status_code == 409
    assert r.json()["detail"] == "no_report_parks"
