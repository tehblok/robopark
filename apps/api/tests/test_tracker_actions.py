import threading
from concurrent.futures import ThreadPoolExecutor

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, Report, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.services import reports as reports_svc
from robopark_api.services.rbac import RoleSlug


def _seed_operator(db_session, park):
    operator = User(
        username="op3",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
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


def test_tracker_action_attach(client, db_session, seed_park_with_tracker, monkeypatch):
    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client

    captured: dict[str, object] = {}

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
        "upload_temp_attachment",
        lambda **_kwargs: "temp-55",
    )

    def _add_comment(**kwargs):
        captured.update(kwargs)
        return {"id": "1", "text": kwargs["text"]}

    monkeypatch.setattr(tracker_client, "add_comment", _add_comment)

    assert (
        client.post("/auth/login", json={"username": "op3", "password": "secret"}).status_code
        == 204
    )
    response = client.post(
        "/tracker/issues/ROBOPARK-1/attachments",
        files={"file": ("photo.jpg", b"fake-image", "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "attach"
    assert captured["attachment_ids"] == ["temp-55"]
    assert captured["text"].startswith("Фото неисправности\n")


def test_tracker_action_attach_rejects_non_image(
    client, db_session, seed_park_with_tracker, monkeypatch
):
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

    assert (
        client.post("/auth/login", json={"username": "op3", "password": "secret"}).status_code
        == 204
    )
    response = client.post(
        "/tracker/issues/ROBOPARK-1/attachments",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "tracker_attachment_invalid_type"


def test_slow_attachment_does_not_block_health(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import tracker_client

    _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _mock_close_tracker(monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def upload(**kwargs):
        started.set()
        assert release.wait(timeout=5)
        return "temp-1"

    monkeypatch.setattr(tracker_client, "upload_temp_attachment", upload)
    monkeypatch.setattr(tracker_client, "add_comment", lambda **kwargs: {"id": "1"})
    assert login_as(client, "op3", "secret").status_code == 204

    with ThreadPoolExecutor(max_workers=2) as executor:
        attachment = executor.submit(
            client.post,
            "/tracker/issues/ROBOPARK-1/attachments",
            files={"file": ("photo.jpg", b"fake-image", "image/jpeg")},
        )
        try:
            assert started.wait(timeout=2)
            health = executor.submit(client.get, "/health")
            assert health.result(timeout=1).status_code == 200
        finally:
            release.set()
        assert attachment.result(timeout=2).status_code == 200


def test_mechanic_can_attach_when_write_disabled(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    platform_settings.set_bool_setting(
        db_session,
        platform_settings.TRACKER_MECHANIC_WRITE_KEY,
        False,
    )

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
        "upload_temp_attachment",
        lambda **_kwargs: "temp-1",
    )
    monkeypatch.setattr(
        tracker_client,
        "add_comment",
        lambda **_kwargs: {"id": "1", "text": "ok"},
    )

    login_as(client, "mech1", "secret")
    response = client.post(
        "/tracker/issues/ROBOPARK-1/attachments",
        files={"file": ("photo.jpg", b"fake-image", "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["action"] == "attach"


def test_attachment_is_denied_without_tracker_attach_permission(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from robopark_api.services import rbac, tracker_client

    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    mechanic = rbac.load_user_with_role(db_session, seed_mechanic.id)
    assert mechanic is not None
    desired = sorted(
        rbac.role_permission_keys(db_session, mechanic) - {rbac.PERMISSION_TRACKER_ATTACH}
    )
    rbac.set_user_effective_permissions(db_session, mechanic, desired)
    db_session.commit()
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
    login_as(client, "mech1", "secret")

    response = client.post(
        "/tracker/issues/ROBOPARK-1/attachments",
        files={"file": ("robot.jpg", b"image", "image/jpeg")},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "tracker_attach_disabled"


def test_mechanic_comment_blocked_when_write_disabled(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    platform_settings.set_bool_setting(
        db_session,
        platform_settings.TRACKER_MECHANIC_WRITE_KEY,
        False,
    )

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

    login_as(client, "mech1", "secret")
    response = client.post("/tracker/issues/ROBOPARK-1/comment", json={"text": "hello"})
    assert response.status_code == 403
    assert response.json()["detail"] == "tracker_write_disabled"


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
    assert report.target_role == RoleSlug.OPERATOR
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
        role_id=role_id_for(db_session, "mechanic"),
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
