from unittest.mock import patch

from conftest import login_as
from robopark_api.models import Park, User, UserPark
from robopark_api.security import hash_password


def seed_op_two_queues(db_session):
    p1 = Park(name="A", tag="A", is_active=True, tracker_queue="Q1")
    p2 = Park(name="B", tag="B", is_active=True, tracker_queue="Q2")
    p3 = Park(name="C", tag="C", is_active=True, tracker_queue=None)
    db_session.add_all([p1, p2, p3])
    db_session.flush()
    op = User(
        username="op-robot",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.flush()
    for p in (p1, p2, p3):
        db_session.add(UserPark(user_id=op.id, park_id=p.id))
    db_session.commit()
    return op


def test_robot_search_no_queues(client, db_session, seed_royal):
    op = User(
        username="op-empty",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.commit()
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-empty", "secret")
    r = client.get("/operator/robots/a447/tickets")
    assert r.status_code == 409
    assert r.json()["detail"] == "no_tracker_parks"


def test_robot_search_merges_and_dedupes(client, db_session, seed_royal):
    seed_op_two_queues(db_session)
    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-robot", "secret")

    def fake_search(*, token, queue, query):
        base = {
            "status": "queued",
            "created": "2026-01-01T10:00:00+00:00",
            "hours_created": "100.0",
            "robot": "a447",
            "in_relocation": "0",
            "status_key": "queued",
            "resolution": "",
        }
        if queue == "Q1":
            return [{**base, "key": "R-1", "summary": "[a447] one"}]
        return [
            {**base, "key": "R-1", "summary": "[a447] one"},
            {
                **base,
                "key": "R-2",
                "summary": "[a447] two",
                "status": "moving",
                "hours_created": "50.0",
                "in_relocation": "1",
                "status_key": "moving",
                "created": "2026-01-02T10:00:00+00:00",
            },
        ]

    with patch(
        "robopark_api.services.tracker_client.search_robot_tickets",
        side_effect=fake_search,
    ):
        r = client.get("/operator/robots/a447/tickets")
    assert r.status_code == 200
    keys = [i["key"] for i in r.json()["items"]]
    assert len(keys) == 2
    assert set(keys) == {"R-1", "R-2"}


def test_robot_search_ticket_key_rejects_foreign_queue(client, db_session, seed_royal):
    p1 = Park(name="Only", tag="Only", is_active=True, tracker_queue="Q1")
    db_session.add(p1)
    db_session.flush()
    op = User(
        username="op-q1",
        password_hash=hash_password("secret"),
        role="operator",
        access_status="approved",
        is_active=True,
    )
    db_session.add(op)
    db_session.flush()
    db_session.add(UserPark(user_id=op.id, park_id=p1.id))
    db_session.commit()

    login_as(client, "royal", "secret")
    client.put("/admin/settings/tracker-token", json={"token": "fake"})
    login_as(client, "op-q1", "secret")

    def fake_search(*, token, queue, query):
        if query.upper() == "FOREIGN-42":
            return [
                {
                    "key": "FOREIGN-42",
                    "summary": "foreign issue",
                    "status": "queued",
                    "created": "2026-01-01T10:00:00+00:00",
                    "hours_created": "1.0",
                    "robot": None,
                    "in_relocation": "0",
                    "status_key": "queued",
                    "resolution": "",
                    "queue": "Q2",
                }
            ]
        return []

    with patch(
        "robopark_api.services.tracker_client.search_robot_tickets",
        side_effect=fake_search,
    ):
        r = client.get("/operator/robots/FOREIGN-42/tickets")
    assert r.status_code == 200
    assert r.json()["items"] == []
