import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker
from starlette.background import BackgroundTasks

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, Report, User, UserPark
from robopark_api.notification_delivery_models import NotificationDelivery
from robopark_api.schedule_models import NotificationEvent, ScheduleEntry
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.task_workflow_models import ReliableAction, TaskReview


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
    return operator


@pytest.mark.parametrize("keyed", [False, True])
def test_tracker_action_comment(
    client, db_session, seed_park_with_tracker, seed_mechanic, monkeypatch, keyed
):
    _seed_operator(db_session, seed_park_with_tracker)
    now = datetime.now(UTC)
    db_session.add(
        ScheduleEntry(
            owner_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            kind="shift",
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user_id=seed_mechanic.id,
            updated_by_user_id=seed_mechanic.id,
        )
    )
    db_session.commit()
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda *_args, **_kwargs: None)
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
            "assignee": {"login": "mech1", "display": "Mechanic"},
        },
    )
    monkeypatch.setattr(tracker_client, "add_comment", lambda **_kwargs: {"id": "1", "text": "ok"})

    assert (
        client.post("/auth/login", json={"username": "op3", "password": "secret"}).status_code
        == 204
    )
    headers = (
        {
            "Idempotency-Key": "comment-key-1",
            "X-Tracker-State": json.dumps(
                {
                    "status": "Open",
                    "status_key": "open",
                    "assignee": "mech1",
                }
            ),
        }
        if keyed
        else {}
    )
    response = client.post(
        "/tracker/issues/ROBOPARK-1/comment", json={"text": "hello"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["action"] == "comment"
    events = list(
        db_session.scalars(
            select(NotificationEvent).where(NotificationEvent.event_type == "operator_comment")
        )
    )
    assert [event.user_id for event in events] == [seed_mechanic.id]


def test_keyed_operator_comment_notification_recovers_after_local_failure(
    client, db_engine, db_session, seed_park_with_tracker, seed_mechanic, monkeypatch
):
    operator = _seed_operator(db_session, seed_park_with_tracker)
    now = datetime.now(UTC)
    db_session.add(
        ScheduleEntry(
            owner_user_id=seed_mechanic.id,
            park_id=seed_park_with_tracker.id,
            kind="shift",
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(hours=1),
            created_by_user_id=seed_mechanic.id,
            updated_by_user_id=seed_mechanic.id,
        )
    )
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client, tracker_outbox

    issue = {
        "key": "ROBOPARK-1",
        "summary": "blocker [447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "resolution": "",
        "tags": ["Alpha"],
        "assignee": {"login": "mech1", "display": "Mechanic"},
    }
    remote_comments = []
    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    monkeypatch.setattr(tracker_client, "list_comments", lambda **_kwargs: list(remote_comments))

    def add_comment(**kwargs):
        remote_comments.append(
            {
                "id": "accepted-1",
                "text": kwargs["text"],
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        return remote_comments[-1]

    monkeypatch.setattr(tracker_client, "add_comment", add_comment)
    login_as(client, operator.username, "secret")
    headers = {
        "Idempotency-Key": "operator-comment-failure-1",
        "X-Tracker-State": json.dumps(
            {"status": "Open", "status_key": "open", "assignee": "mech1"}
        ),
    }
    route = "/tracker/issues/ROBOPARK-1/comment"
    push_service = client.app.state.push_service
    real_emit = push_service.emit_in_transaction

    def fail_local_stage(*_args, **_kwargs):
        raise RuntimeError("notification database unavailable")

    monkeypatch.setattr(push_service, "emit_in_transaction", fail_local_stage)
    with pytest.raises(RuntimeError, match="notification database unavailable"):
        client.post(route, json={"text": "hello"}, headers=headers)
    db_session.rollback()
    action = db_session.scalar(
        select(ReliableAction).where(ReliableAction.idempotency_key == headers["Idempotency-Key"])
    )
    assert action is not None and action.state == "pending"
    assert len(remote_comments) == 1
    assert db_session.scalars(select(NotificationEvent)).all() == []
    # Recovery must use the audience captured before Tracker accepted the
    # comment, even if the shift has ended by the time outbox runs.
    db_session.execute(delete(ScheduleEntry))
    db_session.commit()

    monkeypatch.setattr(push_service, "emit_in_transaction", real_emit)
    factory = sessionmaker(bind=db_engine, future=True)
    assert tracker_outbox._process_batch(factory) == 1
    db_session.expire_all()
    assert db_session.get(ReliableAction, action.id).state == "succeeded"
    events = db_session.scalars(select(NotificationEvent)).all()
    assert len(events) == 1 and events[0].user_id == seed_mechanic.id
    assert len(db_session.scalars(select(NotificationDelivery)).all()) == 1
    assert len(remote_comments) == 1

    replay = client.post(route, json={"text": "hello"}, headers=headers)
    assert replay.status_code == 200
    assert len(db_session.scalars(select(NotificationEvent)).all()) == 1
    assert len(remote_comments) == 1

    db_session.execute(delete(NotificationDelivery))
    db_session.execute(delete(NotificationEvent))
    db_session.commit()
    repaired = client.post(route, json={"text": "hello"}, headers=headers)
    assert repaired.status_code == 200
    assert len(db_session.scalars(select(NotificationEvent)).all()) == 1
    assert len(db_session.scalars(select(NotificationDelivery)).all()) == 1
    assert len(remote_comments) == 1


def test_mechanic_claims_locally_without_tracker_login_or_upstream_assignment(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    seed_mechanic.tracker_login = None
    db_session.commit()
    issue = {
        "key": "ROBOPARK-9",
        "summary": "[447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
    }
    from robopark_api.services import tracker_client

    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    assigned = []
    comments = []
    monkeypatch.setattr(
        tracker_client, "assign_issue", lambda **kwargs: assigned.append(kwargs["assignee"])
    )
    monkeypatch.setattr(
        tracker_client, "add_comment", lambda **kwargs: comments.append(kwargs["text"])
    )
    login_as(client, "mech1", "secret")

    assert (
        client.post("/tracker/issues/ROBOPARK-9/assign", json={"assignee": "other"}).status_code
        == 409
    )
    ok = client.post(
        "/tracker/issues/ROBOPARK-9/claim", headers={"Idempotency-Key": "claim-mech1-0001"}
    )
    assert ok.status_code == 200
    assert assigned == []
    assert comments == []  # Bot delivery is queued, not a synchronous side effect.
    detail = client.get("/tracker/issues/ROBOPARK-9")
    assert detail.status_code == 200
    assert detail.json()["assignee"]["login"] == seed_mechanic.username


def test_legacy_assign_cannot_bypass_durable_workflow(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from sqlalchemy import select

    from robopark_api.services import tracker_client
    from robopark_api.services.tracker_claims import get_claim
    from robopark_api.task_workflow_models import ReliableAction

    issue = {
        "key": "ROBOPARK-legacy",
        "summary": "[447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
    }
    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    login_as(client, seed_mechanic.username, "secret")
    response = client.post(
        f"/tracker/issues/{issue['key']}/assign", json={"assignee": seed_mechanic.username}
    )
    assert response.status_code == 409
    assert get_claim(db_session, issue["key"]) is None
    assert db_session.scalars(select(ReliableAction)).all() == []


def test_mechanic_can_take_over_a_shiftmates_local_claim(
    client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    issue = {
        "key": "ROBOPARK-10",
        "summary": "[447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "tags": ["Alpha"],
    }
    from robopark_api.services import tracker_client
    from robopark_api.services.tracker_claims import claim_issue

    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: dict(issue))
    monkeypatch.setattr(tracker_client, "add_comment", lambda **_kwargs: {"id": "1"})
    claim_issue(
        db_session,
        actor=seed_royal,
        owner=seed_royal,
        issue_key=issue["key"],
        park_id=seed_park_with_tracker.id,
    )
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        f"/tracker/issues/{issue['key']}/claim",
        headers={"Idempotency-Key": "claim-shiftmate-0001"},
    )

    assert response.status_code == 200
    assert (
        client.get(f"/tracker/issues/{issue['key']}").json()["assignee"]["login"]
        == seed_mechanic.username
    )


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
            "assignee": {"login": "mech1", "display": "Mechanic"},
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
            "assignee": {"login": "mech1", "display": "Mechanic"},
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
        assert release.wait(timeout=10)
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
            assert started.wait(timeout=5)
            health = executor.submit(client.get, "/health")
            assert health.result(timeout=3).status_code == 200
        finally:
            release.set()
        assert attachment.result(timeout=5).status_code == 200


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
            "assignee": {"login": "mech1", "display": "Mechanic"},
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

    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-1",
        park_id=seed_park_with_tracker.id,
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
            "assignee": {"login": "mech1", "display": "Mechanic"},
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
            "assignee": {"login": "mech1", "display": "Mechanic"},
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
        "assignee": {"login": "mech1", "display": "Mechanic"},
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


def test_mechanic_cannot_use_legacy_final_close(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _mock_close_tracker(monkeypatch)

    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-1",
        park_id=seed_park_with_tracker.id,
    )

    login_as(client, "mech1", "secret")
    response = client.post("/tracker/issues/ROBOPARK-1/close")

    assert response.status_code == 403
    assert response.json()["detail"] == "task_review_operator_required"
    assert db_session.query(Report).count() == 0


def test_mechanic_close_without_park_is_still_rejected_as_review_approval(
    client, db_session, monkeypatch
):
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

    assert response.status_code == 403
    assert response.json()["detail"] == "task_review_operator_required"
    assert transition_called is False
    assert db_session.query(Report).count() == 0


def test_legacy_close_delegates_to_pending_review_approval_without_upstream_write(
    client,
    db_session,
    seed_mechanic,
    seed_park_with_tracker,
    monkeypatch,
):
    operator = _seed_operator(db_session, seed_park_with_tracker)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    _mock_close_tracker(monkeypatch)
    from robopark_api.services import tracker_client
    from robopark_api.services.tracker_claims import claim_issue

    claim_issue(
        db_session,
        actor=seed_mechanic,
        owner=seed_mechanic,
        issue_key="ROBOPARK-1",
        park_id=seed_park_with_tracker.id,
    )
    db_session.add(
        TaskReview(
            id="legacy-close-review",
            issue_key="ROBOPARK-1",
            state="pending",
            actor_user_id=seed_mechanic.id,
            created_at=1,
            updated_at=1,
        )
    )
    db_session.commit()
    written = []
    monkeypatch.setattr(tracker_client, "transition_issue", lambda **kw: written.append(kw))
    login_as(client, operator.username, "secret")

    response = client.post(
        "/tracker/issues/ROBOPARK-1/close",
        headers={"Idempotency-Key": "legacy-close-pending"},
    )

    assert response.status_code == 200
    assert db_session.query(TaskReview).one().state == "closed"
    assert db_session.query(ReliableAction).filter_by(action="close").count() == 1
    assert written == []
