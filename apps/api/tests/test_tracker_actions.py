from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings


def _seed_operator(db_session, park):
    operator = User(
        username="op3",
        password_hash=hash_password("secret"),
        role="operator",
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(operator)
    db_session.flush()
    db_session.add(UserPark(user_id=operator.id, park_id=park.id))
    db_session.commit()


def test_tracker_action_comment(client, db_session, seed_park_with_tracker, monkeypatch):
    _seed_operator(db_session, seed_park_with_tracker)
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
        },
    )
    monkeypatch.setattr(tracker_client, "add_comment", lambda **_kwargs: {"id": "1", "text": "ok"})

    assert client.post("/auth/login", json={"username": "op3", "password": "secret"}).status_code == 204
    response = client.post("/tracker/issues/ROBOPARK-1/comment", json={"text": "hello"})
    assert response.status_code == 200
    assert response.json()["action"] == "comment"
