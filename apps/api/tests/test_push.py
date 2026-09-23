import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from conftest import login_as, role_id_for
from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.routers import push
from robopark_api.schedule_models import NotificationEvent
from robopark_api.security import hash_password
from robopark_api.services import platform_settings
from robopark_api.services.rbac import RoleSlug
from robopark_api.task_workflow_models import TaskReview

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _wait_until(predicate, *, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


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
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
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

    monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: {
        "key": "ROBOPARK-51", "summary": "blocker [447]", "status": "В работе",
        "status_key": "in_progress", "queue": "ROBOPARK", "tags": [seed_park_with_tracker.tag],
    })
    monkeypatch.setattr(schedules, "resolve_active_operator", resolve_once)
    emitted = []
    monkeypatch.setattr(client.app.state.push_service, "emit", lambda **kwargs: emitted.append(kwargs))
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
    assert resolved_parks == [seed_park_with_tracker.id, seed_park_with_tracker.id]
    assert db_session.query(TaskReview).one().reviewer_user_id == first_operator.id
    assert [event["target_user_ids"] for event in emitted] == [
        {first_operator.id},
        {first_operator.id},
    ]


def test_push_subscription_and_internal_inbox_do_not_expose_task_text(client, seed_mechanic):
    login_as(client, seed_mechanic.username, "secret")
    client.post(
        "/schedules",
        json={
            "park_id": seed_mechanic.parks[0].id,
            "kind": "shift",
            "start_at": "2026-09-20T00:00:00+03:00",
            "end_at": "2026-09-21T23:59:00+03:00",
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


def test_role_matrix_on_shift_and_mandatory_royal_internal_alert(
    client, db_session, seed_mechanic, seed_royal, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, seed_mechanic.username, "secret")
    client.post(
        "/schedules",
        json={
            "park_id": seed_park_with_tracker.id,
            "kind": "shift",
            "start_at": "2026-09-20T00:00:00+03:00",
            "end_at": "2026-09-21T23:59:00+03:00",
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


def test_push_config_and_delivery_send_only_event_identifier(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    config = client.get("/push/config")
    assert config.status_code == 200
    assert len(config.json()["public_key"]) >= 80
    assert (
        client.post(
            "/push/subscriptions",
            json={"endpoint": "https://push.example/real", "p256dh": "key", "auth": "auth"},
        ).status_code
        == 201
    )
    delivered = []
    monkeypatch.setattr(push, "webpush", lambda **kwargs: delivered.append(kwargs))

    event = client.app.state.push_service.emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="Закрытый текст",
        event_key="report:42",
    )

    assert event["recipient_ids"] == [operator.id]
    assert _wait_until(lambda: bool(delivered))
    assert delivered[0]["data"] == f'{{"event_id": "{event["event_id"]}"}}'
    assert "Закрытый" not in delivered[0]["data"]


def test_push_delivery_limits_batch_and_concurrency(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    for index in range(7):
        response = client.post(
            "/push/subscriptions",
            json={
                "endpoint": f"https://push.example/bounded/{index}",
                "p256dh": "key",
                "auth": "auth",
            },
        )
        assert response.status_code == 201

    settings = push.get_settings()
    settings.push_max_concurrency = 2
    settings.push_delivery_batch_size = 5
    settings.push_delivery_deadline_seconds = 1.0
    active = 0
    maximum_active = 0
    delivered = 0
    lock = threading.Lock()

    def bounded_webpush(**_kwargs):
        nonlocal active, delivered, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
            delivered += 1

    monkeypatch.setattr(push, "webpush", bounded_webpush)
    client.app.state.push_service.emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="bounded",
    )

    assert _wait_until(lambda: delivered == 5)
    assert delivered == 5
    assert maximum_active == 2


def test_push_delivery_concurrency_is_shared_across_parallel_emits(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    for index in range(4):
        response = client.post(
            "/push/subscriptions",
            json={
                "endpoint": f"https://push.example/shared/{index}",
                "p256dh": "key",
                "auth": "auth",
            },
        )
        assert response.status_code == 201

    settings = push.get_settings()
    settings.push_max_concurrency = 2
    settings.push_delivery_batch_size = 4
    settings.push_delivery_deadline_seconds = 1.0
    active = 0
    maximum_active = 0
    delivered = 0
    lock = threading.Lock()

    def bounded_webpush(**_kwargs):
        nonlocal active, delivered, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
            delivered += 1

    service = client.app.state.push_service
    monkeypatch.setattr(push, "webpush", bounded_webpush)
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [
            callers.submit(
                service.emit,
                event_type="report",
                park_id=seed_park_with_tracker.id,
                protected_text=f"parallel-{index}",
                event_key=f"parallel:{index}",
            )
            for index in range(2)
        ]
        for future in futures:
            future.result()

    assert _wait_until(lambda: delivered == 8)
    assert maximum_active == 2
    service.close()


def test_push_delivery_returns_at_total_deadline(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    for index in range(3):
        response = client.post(
            "/push/subscriptions",
            json={
                "endpoint": f"https://push.example/slow/{index}",
                "p256dh": "key",
                "auth": "auth",
            },
        )
        assert response.status_code == 201

    settings = push.get_settings()
    settings.push_max_concurrency = 1
    settings.push_delivery_batch_size = 3
    settings.push_delivery_deadline_seconds = 0.03
    monkeypatch.setattr(push, "webpush", lambda **_kwargs: time.sleep(0.2))

    started = time.monotonic()
    client.app.state.push_service.emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="deadline",
    )

    assert time.monotonic() - started < 0.15


def test_slow_push_delivery_does_not_block_emit_or_unrelated_executor_work(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    assert (
        client.post(
            "/push/subscriptions",
            json={
                "endpoint": "https://push.example/async",
                "p256dh": "key",
                "auth": "auth",
            },
        ).status_code
        == 201
    )
    delivery_started = threading.Event()
    finish_delivery = threading.Event()

    def slow_webpush(**_kwargs):
        delivery_started.set()
        finish_delivery.wait(timeout=1)

    monkeypatch.setattr(push, "webpush", slow_webpush)
    started = time.monotonic()
    event = client.app.state.push_service.emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="stored before delivery",
    )
    elapsed = time.monotonic() - started

    try:
        assert event["internal_recipient_ids"] == [operator.id]
        assert elapsed < 0.1
        assert delivery_started.wait(timeout=0.5)
        with ThreadPoolExecutor(max_workers=1) as unrelated:
            assert unrelated.submit(lambda: "ready").result(timeout=0.1) == "ready"
    finally:
        finish_delivery.set()


def test_push_delivery_retries_once_without_blocking_emitter(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    assert (
        client.post(
            "/push/subscriptions",
            json={
                "endpoint": "https://push.example/retry",
                "p256dh": "key",
                "auth": "auth",
            },
        ).status_code
        == 201
    )
    delivered = threading.Event()
    attempts = 0

    def flaky_webpush(**_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary push failure")
        delivered.set()

    monkeypatch.setattr(push, "webpush", flaky_webpush)
    client.app.state.push_service.emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="retry",
    )

    assert delivered.wait(timeout=0.5)
    assert attempts == 2


def test_full_push_queue_drops_only_network_work_and_keeps_internal_events(
    client, db_engine, db_session, seed_park_with_tracker, monkeypatch, caplog
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")
    assert (
        client.post(
            "/push/subscriptions",
            json={
                "endpoint": "https://push.example/backpressure",
                "p256dh": "key",
                "auth": "auth",
            },
        ).status_code
        == 201
    )
    settings = push.get_settings()
    settings.push_max_concurrency = 1
    settings.push_delivery_deadline_seconds = 1.0
    started = threading.Event()
    finish = threading.Event()

    def blocked_webpush(**_kwargs):
        started.set()
        finish.wait(timeout=1)

    monkeypatch.setattr(push, "webpush", blocked_webpush)
    session_factory = sessionmaker(bind=db_engine, future=True)
    service = push.PushService(session_factory, delivery_queue_size=1)
    try:
        service.emit(
            event_type="report",
            park_id=seed_park_with_tracker.id,
            protected_text="first",
            event_key="backpressure:first",
        )
        assert started.wait(timeout=0.5)
        service.emit(
            event_type="report",
            park_id=seed_park_with_tracker.id,
            protected_text="second",
            event_key="backpressure:second",
        )
        with caplog.at_level(logging.WARNING, logger=push.__name__):
            service.emit(
                event_type="report",
                park_id=seed_park_with_tracker.id,
                protected_text="third",
                event_key="backpressure:third",
            )
        assert "Web Push queue full" in caplog.text
        with session_factory() as db:
            assert (
                db.scalar(
                    select(func.count(NotificationEvent.id)).where(
                        NotificationEvent.user_id == operator.id
                    )
                )
                == 3
            )
    finally:
        finish.set()
        service.close()


def test_push_delivery_failure_does_not_lose_internal_notification(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")

    def fail_delivery(*_args, **_kwargs):
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(client.app.state.push_service, "_enqueue_delivery", fail_delivery)
    emitted = client.app.state.push_service.emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="still stored",
    )

    inbox = client.get("/push/inbox").json()
    assert emitted["internal_recipient_ids"] == [operator.id]
    assert inbox[0]["protected_text"] == "still stored"


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
