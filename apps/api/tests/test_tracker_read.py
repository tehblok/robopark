from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings


def _seed_operator(db_session, park):
    operator = User(
        username="op2",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=park.id))
    db_session.commit()
    return operator


def test_tracker_read_list_issues(client, db_session, seed_park_with_tracker, monkeypatch):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [
            {
                "key": "ROBOPARK-1",
                "summary": "blocker [447]",
                "status": "Open",
                "status_key": "open",
                "queue": "ROBOPARK",
                "created": "2026-01-01T00:00:00Z",
                "hours_created": "2.0",
                "robot": "447",
                "resolution": "",
            }
        ],
    )

    assert (
        client.post("/auth/login", json={"username": "op2", "password": "secret"}).status_code
        == 204
    )
    response = client.get("/tracker/issues")
    assert response.status_code == 200
    assert response.json()["items"][0]["key"] == "ROBOPARK-1"


def test_tracker_read_assignee_filter(client, db_session, seed_park_with_tracker, monkeypatch):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client

    captured: dict[str, str] = {}

    def fake_search(**kwargs):
        captured["query"] = kwargs["query"]
        return []

    monkeypatch.setattr(tracker_client, "search_issues", fake_search)

    assert (
        client.post("/auth/login", json={"username": "op2", "password": "secret"}).status_code
        == 204
    )

    response = client.get("/tracker/issues?assignee=ivan.petrov")
    assert response.status_code == 200
    assert "Assignee: ivan.petrov" in captured["query"]

    response = client.get("/tracker/issues?assignee=empty")
    assert response.status_code == 200
    assert "Assignee: empty()" in captured["query"]


def test_mechanic_comments_filtered_to_platform_and_staff(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.models import AccessStatus, User, UserPark
    from robopark_api.security import hash_password

    operator = User(
        username="op_staff",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=seed_park_with_tracker.id))
    db_session.commit()

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-1",
            "summary": "blocker [447]",
            "status": "Open",
            "status_key": "open",
            "queue": "ROBOPARK",
            "resolution": "",
            "tags": ["Alpha"],
        },
    )
    monkeypatch.setattr(
        tracker_client,
        "list_comments",
        lambda **_kwargs: [
            {"id": "1", "text": "ok\nAlpha / mech1 / operator1", "author": "bot", "author_login": "bot"},
            {
                "id": "2",
                "text": "plain comment",
                "author": "Operator",
                "author_login": "op_staff",
            },
            {"id": "3", "text": "random chatter", "author": "human", "author_login": "stranger"},
        ],
    )

    login_as(client, "mech1", "secret")
    response = client.get("/tracker/issues/ROBOPARK-1/comments")
    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload] == ["1", "2"]


def test_tracker_robot_search_royal(client, db_session, seed_royal, seed_park_with_tracker, monkeypatch):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_cache

    monkeypatch.setattr(
        tracker_cache,
        "search_robot_tickets",
        lambda **kwargs: [
            {
                "key": "ROBOPARK-9",
                "summary": "blocker [447]",
                "status": "Open",
                "status_key": "open",
                "queue": kwargs["queue"],
                "created": "2026-01-01T00:00:00Z",
                "hours_created": "1.0",
                "robot": "447",
            }
        ],
    )

    login_as(client, "royal", "secret")
    response = client.get("/tracker/robots/447/tickets")
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "447"
    assert body["items"][0]["key"] == "ROBOPARK-9"
