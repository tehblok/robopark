import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker
from starlette.background import BackgroundTasks

from conftest import login_as, role_id_for
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.notification_delivery_models import NotificationDelivery
from robopark_api.schedule_models import NotificationEvent, PushSubscription, ScheduleEntry
from robopark_api.security import hash_password
from robopark_api.services import notification_delivery, platform_settings
from robopark_api.services.rbac import RoleSlug
from robopark_api.task_workflow_models import TaskReview

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _operator(db, park_id: int) -> User:
    user = User(
        username="operator-push",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, RoleSlug.OPERATOR),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserPark(user_id=user.id, park_id=park_id))
    db.commit()
    db.refresh(user)
    return user


def test_submit_review_replay_keeps_persisted_reviewer_after_shift_change(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch, test_settings
):
    from robopark_api.services import task_timeline

    monkeypatch.setattr(task_timeline, "get_settings", lambda: test_settings)
    first_operator = _operator(db_session, seed_park_with_tracker.id)
    second_operator = User(
        username="operator-next-shift",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, RoleSlug.OPERATOR),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(second_operator)
    db_session.flush()
    db_session.add(UserPark(user_id=second_operator.id, park_id=seed_park_with_tracker.id))
    db_session.add(
        TrackerClaim(
            issue_key="ROBOPARK-51",
            park_id=seed_park_with_tracker.id,
            owner_user_id=seed_mechanic.id,
            updated_by_user_id=seed_mechanic.id,
            state="active",
            updated_at=time.time(),
        )
    )
    db_session.commit()
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")
    from robopark_api.services import schedules, tracker_client

    resolved_parks = []
    reviewers = iter((first_operator, second_operator))

    def resolve_once(db, *, park_id, at=None):
        resolved_parks.append(park_id)
        return next(reviewers)

    monkeypatch.setattr(
        tracker_client,
        "get_issue",
        lambda **_kwargs: {
            "key": "ROBOPARK-51",
            "summary": "blocker [447]",
            "status": "В работе",
            "status_key": "in_progress",
            "queue": "ROBOPARK",
            "tags": [seed_park_with_tracker.tag],
        },
    )
    monkeypatch.setattr(schedules, "resolve_active_operator", resolve_once)
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda *_args, **_kwargs: None)
    login_as(client, seed_mechanic.username, "secret")

    response = client.post(
        "/tracker/issues/ROBOPARK-51/submit-review",
        headers={"Idempotency-Key": "targeted-review"},
        data={"defect_code": "BD-01", "comment": "Исправлено"},
        files=[("photo", ("robot.png", PNG, "image/png"))],
    )
    replay = client.post(
        "/tracker/issues/ROBOPARK-51/submit-review",
        headers={"Idempotency-Key": "targeted-review"},
        data={"defect_code": "BD-01", "comment": "Исправлено"},
        files=[("photo", ("robot.png", PNG, "image/png"))],
    )

    assert response.status_code == replay.status_code == 200
    assert response.json() == replay.json()
    assert resolved_parks == [seed_park_with_tracker.id]
    assert db_session.query(TaskReview).one().reviewer_user_id == first_operator.id
    notifications = list(
        db_session.scalars(
            select(NotificationEvent).where(NotificationEvent.event_type == "review_task")
        )
    )
    assert [event.user_id for event in notifications] == [first_operator.id]

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
    login_as(client, first_operator.username, "secret")
    returned = client.post(
        "/tracker/issues/ROBOPARK-51/review/return",
        headers={"Idempotency-Key": "targeted-return"},
        json={"reason": "Нужно переснять"},
    )
    assert returned.status_code == 200
    returned_notifications = list(
        db_session.scalars(
            select(NotificationEvent).where(NotificationEvent.event_type == "return")
        )
    )
    assert [event.user_id for event in returned_notifications] == [seed_mechanic.id]


def test_push_subscription_and_internal_inbox_do_not_expose_task_text(client, seed_mechanic):
    login_as(client, seed_mechanic.username, "secret")
    now = datetime.now(UTC)
    client.post(
        "/schedules",
        json={
            "park_id": seed_mechanic.parks[0].id,
            "kind": "shift",
            "start_at": (now - timedelta(hours=1)).isoformat(),
            "end_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    subscribed = client.post(
        "/push/subscriptions",
        json={"endpoint": "https://push.example/sub/secret", "p256dh": "key", "auth": "auth"},
    )
    assert subscribed.status_code == 201
    assert "endpoint" not in subscribed.json()

    assert client.put("/push/preferences", json={"categories": ["returns"]}).status_code == 200
    emitted = client.app.state.push_service.emit_for_tests(
        event_type="operator_comment",
        park_id=seed_mechanic.parks[0].id,
        protected_text="секретный текст тикета",
    )
    assert emitted["push_payload"] == {"event_id": emitted["event_id"]}
    assert "секретный" not in str(emitted["push_payload"])
    assert seed_mechanic.id not in emitted["recipient_ids"]
    assert seed_mechanic.id in emitted["internal_recipient_ids"]
    inbox = client.get("/push/inbox").json()
    assert inbox[0]["event_type"] == "operator_comment"
    assert inbox[0]["protected_text"] == "секретный текст тикета"


def test_inbox_hides_park_notification_after_access_is_revoked(client, db_session, seed_mechanic):
    park_id = seed_mechanic.parks[0].id
    event = client.app.state.push_service.emit_for_tests(
        event_type="return",
        park_id=park_id,
        protected_text="Данные задачи бывшего парка",
        recipient_user_ids={seed_mechanic.id},
    )
    notification_id = f"{event['event_id']}-{seed_mechanic.id}"
    login_as(client, seed_mechanic.username, "secret")
    assert [item["id"] for item in client.get("/push/inbox").json()] == [notification_id]

    db_session.execute(
        delete(UserPark).where(UserPark.user_id == seed_mechanic.id, UserPark.park_id == park_id)
    )
    db_session.commit()

    assert client.get("/push/inbox").json() == []
    assert client.post(f"/push/inbox/{notification_id}/read").status_code == 404


def test_push_routes_reject_session_after_user_approval_is_revoked(
    client, db_session, seed_mechanic
):
    event = client.app.state.push_service.emit_for_tests(
        event_type="return",
        park_id=seed_mechanic.parks[0].id,
        protected_text="Текст задачи",
        recipient_user_ids={seed_mechanic.id},
    )
    notification_id = f"{event['event_id']}-{seed_mechanic.id}"
    login_as(client, seed_mechanic.username, "secret")
    assert client.get("/push/inbox").status_code == 200

    seed_mechanic.access_status = AccessStatus.pending.value
    db_session.commit()

    assert client.get("/push/inbox").status_code == 403
    assert client.post(f"/push/inbox/{notification_id}/read").status_code == 403
    assert client.get("/push/config").status_code == 403


def test_user_can_remove_own_device_subscription_after_approval_is_revoked(
    client, db_session, seed_mechanic
):
    login_as(client, seed_mechanic.username, "secret")
    subscription_id = client.post(
        "/push/subscriptions",
        json={"endpoint": "https://push.example/sub/revoked", "p256dh": "key", "auth": "auth"},
    ).json()["id"]
    seed_mechanic.access_status = AccessStatus.pending.value
    db_session.commit()

    assert client.delete(f"/push/subscriptions/{subscription_id}").status_code == 204
    assert db_session.get(PushSubscription, subscription_id) is None


def test_delayed_targeted_notification_skips_former_park_member(client, db_session, seed_mechanic):
    park_id = seed_mechanic.parks[0].id
    db_session.execute(
        delete(UserPark).where(UserPark.user_id == seed_mechanic.id, UserPark.park_id == park_id)
    )
    db_session.commit()

    result = client.app.state.push_service.emit_for_tests(
        event_type="operator_comment",
        park_id=park_id,
        protected_text="Комментарий после смены парка",
        recipient_user_ids={seed_mechanic.id},
    )

    assert result["internal_recipient_ids"] == []
    assert db_session.scalars(select(NotificationEvent)).all() == []


def test_inbox_hides_owner_alert_after_role_is_downgraded(client, db_session, seed_royal):
    event = client.app.state.push_service.emit_for_tests(
        event_type="server_problem",
        park_id=None,
        protected_text="Состояние хоста",
    )
    notification_id = f"{event['event_id']}-{seed_royal.id}"
    login_as(client, seed_royal.username, "secret")
    assert [item["id"] for item in client.get("/push/inbox").json()] == [notification_id]

    seed_royal.role_id = role_id_for(db_session, RoleSlug.MECHANIC)
    db_session.commit()

    assert client.get("/push/inbox").json() == []
    assert client.post(f"/push/inbox/{notification_id}/read").status_code == 404


def test_delayed_targeted_notification_skips_changed_role(client, db_session, seed_mechanic):
    park_id = seed_mechanic.parks[0].id
    seed_mechanic.role_id = role_id_for(db_session, RoleSlug.OPERATOR)
    db_session.commit()

    result = client.app.state.push_service.emit_for_tests(
        event_type="operator_comment",
        park_id=park_id,
        protected_text="Комментарий механику",
        recipient_user_ids={seed_mechanic.id},
    )

    assert result["internal_recipient_ids"] == []
    assert db_session.scalars(select(NotificationEvent)).all() == []


def test_role_matrix_on_shift_and_mandatory_royal_internal_alert(
    client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, seed_mechanic.username, "secret")
    now = datetime.now(UTC)
    client.post(
        "/schedules",
        json={
            "park_id": seed_park_with_tracker.id,
            "kind": "shift",
            "start_at": (now - timedelta(hours=1)).isoformat(),
            "end_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    client.put("/push/preferences", json={"categories": ["new_task"]})

    event = client.app.state.push_service.emit_for_tests(
        event_type="new_task", park_id=seed_park_with_tracker.id, protected_text="task"
    )
    assert seed_mechanic.id in event["recipient_ids"]
    assert operator.id not in event["recipient_ids"]

    client.app.state.push_service.emit_for_tests(
        event_type="server_problem", park_id=None, protected_text="disk"
    )
    login_as(client, seed_royal.username, "secret")
    assert any(item["event_type"] == "server_problem" for item in client.get("/push/inbox").json())


def test_push_config_and_worker_delivery_send_only_event_identifier(
    client, db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    assert client.get("/push/config").status_code == 200
    assert (
        client.post(
            "/push/subscriptions",
            json={"endpoint": "https://push.example/real", "p256dh": "key", "auth": "auth"},
        ).status_code
        == 201
    )

    delivered = []
    monkeypatch.setattr(notification_delivery, "webpush", lambda **kwargs: delivered.append(kwargs))
    event = client.app.state.push_service.emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="Закрытый текст",
        event_key="report:42",
    )

    assert event["recipient_ids"] == [operator.id]
    assert delivered == []
    factory = sessionmaker(bind=db_engine, future=True)
    assert notification_delivery.process_due(factory, owner_id="worker") == 1
    assert delivered[0]["data"] == f'{{"event_id": "{event["event_id"]}"}}'
    assert "Закрытый" not in delivered[0]["data"]


def test_push_backlog_is_durable_and_worker_batch_is_bounded(
    client, db_engine, db_session, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    assert (
        client.post(
            "/push/subscriptions",
            json={"endpoint": "https://push.example/backlog", "p256dh": "key", "auth": "auth"},
        ).status_code
        == 201
    )
    for index in range(3):
        client.app.state.push_service.emit(
            event_type="report",
            park_id=seed_park_with_tracker.id,
            protected_text=f"backlog-{index}",
            event_key=f"backlog:{index}",
        )
    factory = sessionmaker(bind=db_engine, future=True)
    with factory() as db:
        assert db.scalar(select(func.count(NotificationEvent.id))) == 3
        assert (
            db.scalar(
                select(func.count(NotificationDelivery.id)).where(
                    NotificationDelivery.channel == "web_push",
                    NotificationDelivery.state == "pending",
                )
            )
            == 3
        )
    sent = []
    assert (
        notification_delivery.process_due(
            factory, owner_id="worker", limit=2, send_web_push=lambda **kw: sent.append(kw)
        )
        == 2
    )
    assert len(sent) == 2
    with factory() as db:
        assert (
            db.scalar(
                select(func.count(NotificationDelivery.id)).where(
                    NotificationDelivery.channel == "web_push",
                    NotificationDelivery.state == "pending",
                )
            )
            == 1
        )


def test_report_creation_emits_operator_notification(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, seed_mechanic.username, "secret")
    created = client.post(
        "/reports",
        json={
            "kind": "mechanic_problem",
            "park_id": seed_park_with_tracker.id,
            "title": "Нужна помощь",
            "body": "Описание",
        },
    )
    assert created.status_code == 201

    login_as(client, operator.username, "secret")
    inbox = client.get("/push/inbox").json()
    assert any(item["event_type"] == "report" for item in inbox)
