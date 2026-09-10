from conftest import login_as, role_id_for
from robopark_api.services import platform_settings


def test_comment_posts_signed_text_to_tracker(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.models import AccessStatus, User, UserPark
    from robopark_api.security import hash_password

    operator = User(
        username="op_sig",
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

    captured: dict[str, str] = {}

    def _get_issue(**_kwargs):
        return {
            "key": "ROBOPARK-1",
            "summary": "blocker [447]",
            "status": "Open",
            "status_key": "open",
            "queue": "ROBOPARK",
            "resolution": "",
            "tags": ["Alpha"],
            "assignee": {"login": "mech1", "display": "Mechanic One"},
        }

    def _add_comment(**kwargs):
        captured["text"] = kwargs["text"]
        return {"id": "c-42", "text": kwargs["text"]}

    monkeypatch.setattr(tracker_client, "get_issue", _get_issue)
    monkeypatch.setattr(tracker_client, "add_comment", _add_comment)

    login_as(client, "op_sig", "secret")
    response = client.post("/tracker/issues/ROBOPARK-1/comment", json={"text": "Проверил"})
    assert response.status_code == 200

    assert captured["text"].startswith("Проверил\n\n—\nВремя: ")
    assert "\nПарк: Alpha" in captured["text"]
    assert "\nИнициатор: op_sig" in captured["text"]
    assert "\nМеханик: —" in captured["text"]
    assert "\nОператор: op_sig" in captured["text"]
