import json
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.task_workflow_models import ReliableAction, TaskMessage

ISSUE = {
    "key": "ROBOPARK-1",
    "summary": "blocker [447]",
    "status": "Open",
    "status_key": "open",
    "queue": "ROBOPARK",
    "tags": ["Alpha"],
}


def test_timeline_orders_merges_deduplicates_and_hides_action_secrets(
    client, db_session, seed_royal, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "secret-token")
    action = ReliableAction(
        id="action-pending",
        actor_user_id=seed_royal.id,
        resource_type="tracker_issue",
        resource_id="ROBOPARK-1",
        action="comment",
        idempotency_key="message-0001",
        payload_hash="a" * 64,
        payload_json=json.dumps({"text": "local text", "token": "payload-secret"}),
        state="retry_wait",
        next_attempt_at=99,
        created_at=2,
        updated_at=2,
    )
    db_session.add_all(
        [
            action,
            TaskMessage(
                id="local-system",
                issue_key="ROBOPARK-1",
                kind="system",
                author_name="СУРП",
                text="Взято в работу",
                sync_state="saved",
                created_at=1,
                updated_at=1,
            ),
            TaskMessage(
                id="local-delivered",
                issue_key="ROBOPARK-1",
                kind="user",
                author_user_id=seed_royal.id,
                author_name="royal",
                text="local delivered copy",
                external_id="tracker-2",
                sync_state="synced",
                created_at=3,
                updated_at=3,
            ),
            TaskMessage(
                id="local-pending",
                issue_key="ROBOPARK-1",
                kind="user",
                author_user_id=seed_royal.id,
                author_name="royal",
                text="local text",
                action_id=action.id,
                sync_state="pending",
                created_at=2,
                updated_at=2,
            ),
        ]
    )
    db_session.commit()

    from robopark_api.services import tracker_cache

    monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: dict(ISSUE))
    monkeypatch.setattr(
        tracker_cache,
        "list_comments",
        lambda **_kwargs: [
            {
                "id": "tracker-1",
                "text": "external first",
                "author": "Operator",
                "author_login": "operator",
                "created_at": "1970-01-01T00:00:00",
                "attachments": [],
            },
            {
                "id": "tracker-2",
                "text": "external duplicate",
                "author": "Bot",
                "author_login": "bot",
                "created_at": "1970-01-01T00:00:03Z",
                "attachments": [
                    {
                        "id": "photo-1",
                        "name": "done.png",
                        "size": 68,
                        "url": "https://tracker/file",
                        "mimetype": "image/png",
                    }
                ],
            },
        ],
    )
    login_as(client, "royal", "secret")

    first = client.get("/tracker/issues/ROBOPARK-1/timeline")
    second = client.get("/tracker/issues/ROBOPARK-1/timeline")

    assert first.status_code == second.status_code == 200
    items = first.json()
    assert [item["id"] for item in items] == [
        "tracker:ROBOPARK-1:tracker-1",
        "local-system",
        "local-pending",
        "local-delivered",
    ]
    assert items[2]["sync_state"] == "pending"
    assert items[0]["created_at"] == "1970-01-01T00:00:00+00:00"
    assert items[3]["attachments"] == [
        {
            "id": "photo-1",
            "name": "done.png",
            "size": 68,
            "url": "https://tracker/file",
            "mimetype": "image/png",
        }
    ]
    serialized = first.text
    assert "payload_json" not in serialized
    assert "payload-secret" not in serialized
    assert "secret-token" not in serialized
    assert second.json() == items
    imported = db_session.scalars(select(TaskMessage).where(TaskMessage.kind == "tracker")).all()
    assert [(row.external_id, row.text) for row in imported] == [("tracker-1", "external first")]


def test_post_message_commits_local_message_and_reliable_action_together(
    client, db_session, seed_royal, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_cache

    monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: dict(ISSUE))
    login_as(client, "royal", "secret")

    response = client.post(
        "/tracker/issues/ROBOPARK-1/messages",
        headers={"Idempotency-Key": "message-create-0001"},
        json={"text": "Заменил крепёж"},
    )

    assert response.status_code == 201
    message = db_session.get(TaskMessage, response.json()["id"])
    action = db_session.get(ReliableAction, message.action_id)
    assert (message.text, message.sync_state) == ("Заменил крепёж", "pending")
    assert (action.action, action.state) == ("comment", "pending")
    assert json.loads(action.payload_json) == {"text": "Заменил крепёж"}


def test_successful_message_retry_returns_existing_message(db_session, seed_royal):
    from robopark_api.services.reliable_actions import complete_action
    from robopark_api.services.task_timeline import append_user_message

    first = append_user_message(
        db_session,
        issue_key="ROBOPARK-1",
        actor=seed_royal,
        text="Заменил крепёж",
        idempotency_key="message-replay-0001",
    )
    action = db_session.get(ReliableAction, first.action_id)
    complete_action(db_session, action, {"external_id": "tracker-42"})
    db_session.commit()

    replay = append_user_message(
        db_session,
        issue_key="ROBOPARK-1",
        actor=seed_royal,
        text="Заменил крепёж",
        idempotency_key="message-replay-0001",
    )

    assert replay.id == first.id
    assert db_session.query(TaskMessage).filter_by(action_id=action.id).count() == 1


def test_read_only_user_can_read_timeline(client, db_session, seed_park_with_tracker, monkeypatch):
    driver = User(
        username="driver-timeline",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "driver"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(driver)
    db_session.flush()
    db_session.add(UserPark(user_id=driver.id, park_id=seed_park_with_tracker.id))
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    db_session.commit()
    from robopark_api.services import tracker_cache

    issue = {**ISSUE, "status": "Новый", "status_key": "new"}
    monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: issue)
    monkeypatch.setattr(tracker_cache, "list_comments", lambda **_kwargs: [])
    login_as(client, "driver-timeline", "secret")

    response = client.get("/tracker/issues/ROBOPARK-1/timeline")

    assert response.status_code == 200
    assert response.json() == []


def test_tracker_comment_ids_are_stable_and_scoped_to_issue(db_session):
    from robopark_api.services.task_timeline import merge_timeline

    comment = {
        "id": "same-comment-id",
        "text": "one",
        "author": "Tracker",
        "created_at": "2026-09-15T12:00:00Z",
    }

    first = merge_timeline(db_session, issue_key="ROBOPARK-1", comments=[comment])
    second = merge_timeline(db_session, issue_key="ROBOPARK-2", comments=[comment])

    assert first[0]["id"] == "tracker:ROBOPARK-1:same-comment-id"
    assert second[0]["id"] == "tracker:ROBOPARK-2:same-comment-id"


def test_mechanic_timeline_keeps_existing_comment_visibility_policy(
    client, db_session, seed_mechanic, monkeypatch
):
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import tracker_cache

    monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: dict(ISSUE))
    from robopark_api.services.task_timeline import append_system_message, merge_timeline

    merge_timeline(
        db_session,
        issue_key="ROBOPARK-1",
        comments=[
            {
                "id": "signed",
                "text": "ok\nAlpha / mech1 / operator1",
                "author": "Robopark bot",
                "author_login": "bot",
                "created_at": "2026-09-15T12:00:00Z",
            },
            {
                "id": "stranger",
                "text": "private chatter",
                "author": "Human display",
                "author_login": "stranger",
                "created_at": "2026-09-15T12:01:00Z",
            },
        ],
    )
    append_system_message(
        db_session,
        issue_key="ROBOPARK-1",
        actor=seed_mechanic,
        text="local system event",
    )
    db_session.commit()
    monkeypatch.setattr(tracker_cache, "list_comments", lambda **_kwargs: [])
    login_as(client, "mech1", "secret")

    response = client.get("/tracker/issues/ROBOPARK-1/timeline")

    assert response.status_code == 200
    assert [item["text"] for item in response.json()] == [
        "ok\nAlpha / mech1 / operator1",
        "local system event",
    ]


def test_concurrent_tracker_import_is_conflict_safe_and_rereads_winner(db_engine):
    from robopark_api.services.task_timeline import merge_timeline

    barrier = threading.Barrier(2)

    class PausingSession(Session):
        paused = False

        def scalars(self, statement, *args, **kwargs):
            result = super().scalars(statement, *args, **kwargs)
            if not self.paused and "FROM task_messages" in str(statement):
                self.paused = True
                barrier.wait(timeout=5)
            return result

    comment = {
        "id": "concurrent-comment",
        "text": "one imported message",
        "author": "bot",
        "author_login": "bot",
        "created_at": "2026-09-15T12:00:00Z",
    }

    def import_once():
        with PausingSession(db_engine) as db:
            return merge_timeline(db, issue_key="ROBOPARK-1", comments=[comment])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: import_once(), range(2)))

    assert [[item["text"] for item in result] for result in results] == [
        ["one imported message"],
        ["one imported message"],
    ]
    with Session(db_engine) as db:
        assert db.query(TaskMessage).filter_by(external_id="concurrent-comment").count() == 1
