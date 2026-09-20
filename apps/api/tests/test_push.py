import threading
import time
from concurrent.futures import ThreadPoolExecutor

from conftest import login_as, role_id_for
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.routers import push
from robopark_api.security import hash_password
from robopark_api.services.rbac import RoleSlug


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
    lock = threading.Lock()

    def bounded_webpush(**_kwargs):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1

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


def test_push_delivery_failure_does_not_lose_internal_notification(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    login_as(client, operator.username, "secret")

    def fail_delivery(*_args, **_kwargs):
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(client.app.state.push_service, "_deliver", fail_delivery)
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
