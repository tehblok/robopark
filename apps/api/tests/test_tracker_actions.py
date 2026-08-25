from conftest import login_as
from robopark_api.models import AccessStatus, Report, User, UserPark, UserRole
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.services import reports as reports_svc


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
            "tags": ["Alpha"],
        },
    )
    monkeypatch.setattr(tracker_client, "add_comment", lambda **_kwargs: {"id": "1", "text": "ok"})

    assert (
        client.post("/auth/login", json={"username": "op3", "password": "secret"}).status_code
        == 204
    )
    response = client.post("/tracker/issues/ROBOPARK-1/comment", json={"text": "hello"})
    assert response.status_code == 200
    assert response.json()["action"] == "comment"


def _mock_close_tracker(monkeypatch, *, key: str = "ROBOPARK-1"):
    from robopark_api.services import tracker_client

    issue = {
        "key": key,
        "summary": "blocker [447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "resolution": "",
        "tags": ["Alpha"],
    }
    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: issue)
    monkeypatch.setattr(
        tracker_client,
        "list_transitions",
        lambda **_kwargs: [{"id": "close", "display": "Закрыть"}],
    )
    monkeypatch.setattr(
        tracker_client,
        "transition_issue",
        lambda **_kwargs: {"status": "closed"},
    )
    return issue


def test_mechanic_close_creates_close_review(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _mock_close_tracker(monkeypatch)

    login_as(client, "mech1", "secret")
    response = client.post("/tracker/issues/ROBOPARK-1/close")

    assert response.status_code == 200
    assert response.json()["action"] == "close"

    report = db_session.query(Report).one()
    assert report.kind == reports_svc.KIND_TICKET_CLOSE_REVIEW
    assert report.status == reports_svc.STATUS_OPEN
    assert report.park_id == seed_park_with_tracker.id
    assert report.author_user_id == seed_mechanic.id
    assert report.target_role == UserRole.operator.value
    assert report.tracker_key == "ROBOPARK-1"
    assert report.tracker_url == "https://st.yandex-team.ru/ROBOPARK-1"
    assert report.title == "Закрытие ROBOPARK-1"


def test_mechanic_close_without_park_rejected_before_tracker(client, db_session, monkeypatch):
    from robopark_api.services import tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _mock_close_tracker(monkeypatch)

    mechanic = User(
        username="mech_nopark",
        password_hash=hash_password("secret"),
        role=UserRole.mechanic.value,
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(mechanic)
    db_session.commit()

    transition_called = False

    def _transition(**_kwargs):
        nonlocal transition_called
        transition_called = True
        return {"status": "closed"}

    monkeypatch.setattr(tracker_client, "transition_issue", _transition)

    login_as(client, "mech_nopark", "secret")
    response = client.post("/tracker/issues/ROBOPARK-1/close")

    assert response.status_code == 400
    assert response.json()["detail"] == "mechanic_park_required_for_close_review"
    assert transition_called is False
    assert db_session.query(Report).count() == 0
