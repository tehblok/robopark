"""Durable notification delivery contracts."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import sessionmaker
from starlette.background import BackgroundTasks

from conftest import role_id_for
from robopark_api.crypto import encrypt_secret
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.notification_delivery_models import NotificationDelivery
from robopark_api.routers.push import PushService
from robopark_api.schedule_models import NotificationEvent, PushSubscription, ScheduleEntry
from robopark_api.security import hash_password
from robopark_api.services import notification_delivery
from robopark_api.services.rbac import RoleSlug


def _operator(db, park_id):
    user = User(
        username="delivery-operator",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, RoleSlug.OPERATOR),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(UserPark(user_id=user.id, park_id=park_id))
    db.commit()
    return user


def _subscribe(db, user_id, endpoint_hash="endpoint-1"):
    key = "test-suite-secret-key"
    db.add(
        PushSubscription(
            user_id=user_id,
            endpoint_hash=endpoint_hash,
            endpoint_encrypted=encrypt_secret("https://push.example/" + endpoint_hash, key),
            p256dh_encrypted=encrypt_secret("p256dh", key),
            auth_encrypted=encrypt_secret("auth", key),
        )
    )
    db.commit()


def test_emit_persists_web_push_work_before_any_network_attempt(
    db_engine, db_session, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    service = PushService(sessionmaker(bind=db_engine, future=True))
    service.emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="private",
        event_key="durable:1",
    )

    assert "notification_deliveries" in inspect(db_engine).get_table_names()
    with sessionmaker(bind=db_engine, future=True)() as db:
        from robopark_api.notification_delivery_models import NotificationDelivery

        rows = list(db.scalars(select(NotificationDelivery)))
    assert len(rows) == 2
    assert {row.channel for row in rows} == {"in_app", "web_push"}
    assert {row.state for row in rows} == {"delivered", "pending"}


def test_report_mutation_commits_notification_even_when_response_callbacks_never_run(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from conftest import login_as
    from robopark_api.models import Report

    _operator(db_session, seed_park_with_tracker.id)
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda *_args, **_kwargs: None)
    login_as(client, seed_mechanic.username, "secret")
    response = client.post(
        "/reports",
        json={
            "kind": "mechanic_problem",
            "park_id": seed_park_with_tracker.id,
            "title": "Crash window",
            "body": "Persist before response",
        },
    )
    assert response.status_code == 201
    assert db_session.get(Report, response.json()["id"]) is not None
    assert (
        db_session.scalar(
            select(NotificationDelivery).where(NotificationDelivery.channel == "in_app")
        )
        is not None
    )


def test_report_return_and_resubmit_commit_their_notifications(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from conftest import login_as

    operator = _operator(db_session, seed_park_with_tracker.id)
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
    login_as(client, seed_mechanic.username, "secret")
    created = client.post(
        "/reports",
        json={
            "kind": "mechanic_problem",
            "park_id": seed_park_with_tracker.id,
            "title": "Report",
            "body": "Body",
        },
    )
    assert created.status_code == 201
    login_as(client, operator.username, "secret")
    returned = client.post(f"/reports/{created.json()['id']}/return", json={"comment": "Fix it"})
    assert returned.status_code == 200
    login_as(client, seed_mechanic.username, "secret")
    resubmitted = client.post(
        f"/reports/{created.json()['id']}/resubmit",
        json={
            "title": "Revised",
            "body": "Updated",
        },
    )
    assert resubmitted.status_code == 200
    events = list(
        db_session.scalars(select(NotificationEvent).order_by(NotificationEvent.created_at))
    )
    assert [(event.event_type, event.user_id) for event in events] == [
        ("report", operator.id),
        ("return", seed_mechanic.id),
        ("report", operator.id),
    ]


def test_report_and_notification_roll_back_together_when_persistence_fails(
    client, db_session, seed_mechanic, seed_park_with_tracker, monkeypatch
):
    from conftest import login_as
    from robopark_api.models import Report

    _operator(db_session, seed_park_with_tracker.id)

    def fail(_db, **_kwargs):
        raise RuntimeError("notification write failed")

    monkeypatch.setattr(client.app.state.push_service, "emit_in_transaction", fail)
    login_as(client, seed_mechanic.username, "secret")
    with pytest.raises(RuntimeError, match="notification write failed"):
        client.post(
            "/reports",
            json={
                "kind": "mechanic_problem",
                "park_id": seed_park_with_tracker.id,
                "title": "Must roll back",
                "body": "Body",
            },
        )
    db_session.rollback()
    assert db_session.scalar(select(Report).where(Report.title == "Must roll back")) is None


def test_staged_notification_does_not_commit_domain_transaction(
    db_engine, db_session, seed_mechanic, seed_park_with_tracker
):
    from robopark_api.models import Report

    _operator(db_session, seed_park_with_tracker.id)
    factory = sessionmaker(bind=db_engine, future=True)
    with factory() as db:
        report = Report(
            kind="mechanic_problem",
            status="open",
            park_id=seed_park_with_tracker.id,
            author_user_id=seed_mechanic.id,
            target_role="operator",
            title="Rollback staged event",
            body="Body",
        )
        db.add(report)
        db.flush()
        PushService(factory).emit_in_transaction(
            db,
            event_type="report",
            park_id=seed_park_with_tracker.id,
            protected_text="Rollback staged event",
            event_key="rollback:staged",
        )
        db.flush()
        db.rollback()
    with factory() as db:
        assert db.scalar(select(Report).where(Report.title == "Rollback staged event")) is None
        assert (
            db.scalar(
                select(NotificationEvent).where(
                    NotificationEvent.protected_text == "Rollback staged event"
                )
            )
            is None
        )
        assert db.scalar(select(NotificationDelivery)) is None


def test_tail_row_lease_uses_fresh_clock_and_prevents_second_worker_send(
    db_engine, db_session, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    service = PushService(factory)
    for index in range(2):
        service.emit(
            event_type="report",
            park_id=seed_park_with_tracker.id,
            protected_text="private",
            event_key=f"tail:{index}",
        )
    now = [datetime.now(UTC)]
    sent = []
    stolen = []

    def send(**payload):
        sent.append(payload)
        if len(sent) == 1:
            now[0] += timedelta(seconds=61)
        else:
            stolen.append(
                notification_delivery.process_due(
                    factory,
                    owner_id="worker-b",
                    clock=lambda: now[0],
                    send_web_push=lambda **kw: sent.append(kw),
                )
            )

    assert (
        notification_delivery.process_due(
            factory,
            owner_id="worker-a",
            clock=lambda: now[0],
            send_web_push=send,
        )
        == 2
    )
    assert len(sent) == 2
    assert stolen == [0]


def test_expired_lease_during_payload_preparation_blocks_provider_call(
    db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    PushService(factory).emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="private",
        event_key="prepare:slow",
    )
    now = [datetime.now(UTC)]
    real_decrypt = notification_delivery.decrypt_secret
    advanced = [False]

    def slow_decrypt(*args):
        if not advanced[0]:
            now[0] += timedelta(seconds=61)
            advanced[0] = True
        return real_decrypt(*args)

    monkeypatch.setattr(notification_delivery, "decrypt_secret", slow_decrypt)
    sent = []
    notification_delivery.process_due(
        factory,
        owner_id="worker-a",
        clock=lambda: now[0],
        send_web_push=lambda **kw: sent.append(kw),
    )
    assert sent == []
    assert (
        notification_delivery.process_due(
            factory,
            owner_id="worker-b",
            clock=lambda: now[0],
            send_web_push=lambda **kw: sent.append(kw),
        )
        == 1
    )
    assert len(sent) == 1


def test_worker_replays_committed_delivery_after_service_restart(
    db_engine, db_session, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    emitted = PushService(factory).emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="private",
        event_key="restart:1",
    )
    sent = []
    assert (
        notification_delivery.process_due(
            factory, owner_id="restarted", send_web_push=lambda **kw: sent.append(kw)
        )
        == 1
    )
    assert sent[0]["data"] == f'{{"event_id": "{emitted["event_id"]}"}}'
    assert (
        notification_delivery.process_due(
            factory, owner_id="next", send_web_push=lambda **kw: sent.append(kw)
        )
        == 0
    )
    assert len(sent) == 1


def test_only_one_worker_claims_a_delivery(db_engine, db_session, seed_park_with_tracker):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    PushService(factory).emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="private",
        event_key="lease:1",
    )
    with factory() as db:
        row = db.scalar(
            select(NotificationDelivery).where(NotificationDelivery.channel == "web_push")
        )
        row.lease_owner = "other-worker"
        row.lease_until = datetime.now(UTC) + timedelta(minutes=1)
        db.commit()
    sent = []
    assert (
        notification_delivery.process_due(
            factory, owner_id="new-worker", send_web_push=lambda **kw: sent.append(kw)
        )
        == 0
    )
    assert sent == []


def test_parallel_workers_send_one_network_attempt(db_engine, db_session, seed_park_with_tracker):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    PushService(factory).emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="private",
        event_key="parallel:1",
    )
    entered = threading.Event()
    release = threading.Event()
    sent = []

    def slow_send(**payload):
        sent.append(payload)
        entered.set()
        assert release.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(
            notification_delivery.process_due, factory, owner_id="worker-a", send_web_push=slow_send
        )
        assert entered.wait(timeout=2)
        second = workers.submit(
            notification_delivery.process_due, factory, owner_id="worker-b", send_web_push=slow_send
        )
        assert second.result(timeout=2) == 0
        release.set()
        assert first.result(timeout=2) == 1
    assert len(sent) == 1


def test_expired_lease_is_replayed_after_worker_crash(
    db_engine, db_session, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    PushService(factory).emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="private",
        event_key="crash:1",
    )
    with factory() as db:
        row = db.scalar(
            select(NotificationDelivery).where(NotificationDelivery.channel == "web_push")
        )
        row.lease_owner = "dead-worker"
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    sent = []
    assert (
        notification_delivery.process_due(
            factory, owner_id="replacement", send_web_push=lambda **kw: sent.append(kw)
        )
        == 1
    )
    assert len(sent) == 1
    with factory() as db:
        assert (
            db.scalar(
                select(NotificationDelivery).where(NotificationDelivery.channel == "web_push")
            ).state
            == "delivered"
        )


def test_repeated_event_key_creates_one_durable_delivery(
    db_engine, db_session, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    service = PushService(factory)
    for _ in range(2):
        service.emit(
            event_type="report",
            park_id=seed_park_with_tracker.id,
            protected_text="private",
            event_key="same-key",
        )
    with factory() as db:
        rows = list(
            db.scalars(
                select(NotificationDelivery).where(NotificationDelivery.channel == "web_push")
            )
        )
    assert len(rows) == 1
    assert rows[0].idempotency_key.endswith(":web_push:endpoint-1")


@pytest.mark.parametrize("response_code", [404, 410])
def test_gone_subscription_is_removed(db_engine, db_session, seed_park_with_tracker, response_code):
    from pywebpush import WebPushException

    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    PushService(factory).emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="private",
        event_key="gone:1",
    )

    def gone(**_kw):
        error = WebPushException("gone")
        error.response = type("Response", (), {"status_code": response_code})()
        raise error

    assert notification_delivery.process_due(factory, owner_id="worker", send_web_push=gone) == 1
    with factory() as db:
        assert db.scalar(select(PushSubscription)) is None
        assert (
            db.scalar(
                select(NotificationDelivery).where(NotificationDelivery.channel == "web_push")
            ).state
            == "cancelled"
        )


def test_transient_failure_retries_until_24_hour_expiry(
    db_engine, db_session, seed_park_with_tracker
):
    operator = _operator(db_session, seed_park_with_tracker.id)
    _subscribe(db_session, operator.id)
    factory = sessionmaker(bind=db_engine, future=True)
    PushService(factory).emit(
        event_type="report",
        park_id=seed_park_with_tracker.id,
        protected_text="private",
        event_key="retry:1",
    )
    with factory() as db:
        row = db.scalar(
            select(NotificationDelivery).where(NotificationDelivery.channel == "web_push")
        )
        created = row.next_attempt_at

    def fail(**_kw):
        raise OSError("transient")

    assert (
        notification_delivery.process_due(
            factory, owner_id="worker", now=created + timedelta(seconds=1), send_web_push=fail
        )
        == 1
    )
    with factory() as db:
        row = db.scalar(
            select(NotificationDelivery).where(NotificationDelivery.channel == "web_push")
        )
        assert row.state == "pending"
        assert row.attempts == 1
        assert row.next_attempt_at > created + timedelta(seconds=1)
        assert row.expires_at == created + timedelta(hours=24)
    assert (
        notification_delivery.process_due(
            factory, owner_id="worker", now=created + timedelta(hours=25), send_web_push=fail
        )
        == 0
    )
    with factory() as db:
        assert (
            db.scalar(
                select(NotificationDelivery).where(NotificationDelivery.channel == "web_push")
            ).state
            == "expired"
        )


@pytest.mark.parametrize("leave_kind", ["vacation", "sick"])
def test_leave_overrides_shift_and_royal_critical_bypasses_shift(
    db_session, seed_park_with_tracker, seed_royal, leave_kind
):
    from robopark_api.services.schedules import RoutingEvent, eligible_recipients

    mechanic = User(
        username="delivery-mechanic",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, RoleSlug.MECHANIC),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(mechanic)
    db_session.flush()
    db_session.add(UserPark(user_id=mechanic.id, park_id=seed_park_with_tracker.id))
    at = datetime(2026, 9, 24, 12, tzinfo=UTC)
    for kind in ("shift", leave_kind):
        db_session.add(
            ScheduleEntry(
                owner_user_id=mechanic.id,
                park_id=seed_park_with_tracker.id,
                kind=kind,
                start_at=at - timedelta(hours=1),
                end_at=at + timedelta(hours=1),
                created_by_user_id=seed_royal.id,
                updated_by_user_id=seed_royal.id,
            )
        )
    db_session.commit()
    task = RoutingEvent(db_session, "new_task", seed_park_with_tracker.id)
    critical = RoutingEvent(db_session, "disk_low", None)
    assert mechanic.id not in {user.id for user in eligible_recipients(task, at)}
    assert seed_royal.id in {user.id for user in eligible_recipients(critical, at)}


def test_four_on_four_off_shift_entries_route_only_work_days(
    db_session, seed_park_with_tracker, seed_royal
):
    from robopark_api.services.schedules import RoutingEvent, eligible_recipients

    mechanic = User(
        username="four-four-mechanic",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, RoleSlug.MECHANIC),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(mechanic)
    db_session.flush()
    db_session.add(UserPark(user_id=mechanic.id, park_id=seed_park_with_tracker.id))
    moscow = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 9, 1, 9, tzinfo=moscow)
    series_id = "four-four-series"
    for day in (0, 1, 2, 3, 8):
        db_session.add(
            ScheduleEntry(
                owner_user_id=mechanic.id,
                park_id=seed_park_with_tracker.id,
                kind="shift",
                series_id=series_id,
                start_at=start + timedelta(days=day),
                end_at=start + timedelta(days=day, hours=8),
                created_by_user_id=seed_royal.id,
                updated_by_user_id=seed_royal.id,
            )
        )
    db_session.commit()
    event = RoutingEvent(db_session, "new_task", seed_park_with_tracker.id)

    def routed(day):
        return mechanic.id in {
            u.id for u in eligible_recipients(event, start + timedelta(days=day, hours=2))
        }

    assert [routed(day) for day in (0, 3, 4, 7, 8)] == [True, True, False, False, True]


def test_unresolved_park_still_obeys_schedule(db_session, seed_mechanic, seed_royal):
    from robopark_api.services.schedules import RoutingEvent, eligible_recipients

    at = datetime(2026, 9, 24, 12, tzinfo=UTC)
    db_session.add(
        ScheduleEntry(
            owner_user_id=seed_mechanic.id,
            park_id=1,
            kind="sick",
            start_at=at - timedelta(hours=1),
            end_at=at + timedelta(hours=1),
            created_by_user_id=seed_royal.id,
            updated_by_user_id=seed_royal.id,
        )
    )
    db_session.commit()
    event = RoutingEvent(db_session, "operator_comment", None, {seed_mechanic.id})
    assert eligible_recipients(event, at) == []
