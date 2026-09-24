"""Durable notification delivery contracts."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import sessionmaker

from conftest import role_id_for
from robopark_api.crypto import encrypt_secret
from robopark_api.models import AccessStatus, User, UserPark
from robopark_api.notification_delivery_models import NotificationDelivery
from robopark_api.routers.push import PushService
from robopark_api.schedule_models import PushSubscription, ScheduleEntry
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
    for day in (0, 1, 2, 3, 8):
        db_session.add(
            ScheduleEntry(
                owner_user_id=mechanic.id,
                park_id=seed_park_with_tracker.id,
                kind="shift",
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
