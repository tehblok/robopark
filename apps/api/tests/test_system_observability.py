import asyncio
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import sessionmaker

from conftest import login_as
from robopark_api.models import UserPark
from robopark_api.task_workflow_models import ReliableAction


def test_presence_is_database_backed_and_expires_after_two_minutes(
    client, db_session, seed_royal, seed_mechanic, seed_park_with_tracker
):
    login_as(client, "mech1", "secret")
    assert client.post("/presence/heartbeat").status_code == 204
    login_as(client, "royal", "secret")
    summary = client.get("/admin/system/summary")
    assert summary.status_code == 200
    assert summary.json()["online"]["by_role"]["mechanic"] == 1
    assert summary.json()["online"]["by_park"][str(seed_park_with_tracker.id)] == 1

    from robopark_api.services.system_observability import UserPresence

    presence = db_session.get(UserPresence, seed_mechanic.id)
    presence.last_seen_at = datetime.now(UTC) - timedelta(minutes=3)
    db_session.commit()
    assert client.get("/admin/system/summary").json()["online"]["total"] == 0


def test_admin_system_history_requires_admin_and_bounds_days(client, seed_mechanic, seed_royal):
    login_as(client, "mech1", "secret")
    assert client.get("/admin/system/history").status_code == 403
    login_as(client, "royal", "secret")
    assert client.get("/admin/system/history?days=8").status_code == 422
    response = client.get("/admin/system/history?days=7")
    assert response.status_code == 200
    assert set(response.json()) >= {"active_users", "metrics"}


def test_worker_metric_sample_and_retention_are_bounded(db_session):
    from robopark_api.services.system_observability import (
        MetricAggregate,
        MetricRaw,
        collect_system_metrics,
        prune_observability,
    )

    now = datetime(2026, 9, 24, 12, 4, tzinfo=UTC)
    collect_system_metrics(db_session, now=now)
    collect_system_metrics(db_session, now=now + timedelta(seconds=30))
    assert db_session.query(MetricRaw).count() == 2
    assert db_session.query(MetricAggregate).count() == 1
    raw, aggregate = prune_observability(db_session, now=now + timedelta(days=8))
    assert (raw, aggregate) == (2, 1)
    assert prune_observability(db_session, now=now + timedelta(days=8)) == (0, 0)


def test_five_minute_aggregate_averages_queue_depth(db_session, seed_mechanic):
    from robopark_api.services.system_observability import MetricAggregate, collect_system_metrics

    now = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    collect_system_metrics(db_session, now=now)
    db_session.add(
        ReliableAction(
            id="metric-pending-action",
            actor_user_id=seed_mechanic.id,
            resource_type="tracker_issue",
            resource_id="ROBOPARK-5",
            action="comment",
            idempotency_key="metric-pending-key",
            payload_hash="0" * 64,
            payload_json="{}",
            state="pending",
            next_attempt_at=now.timestamp(),
            created_at=now.timestamp(),
            updated_at=now.timestamp(),
        )
    )
    db_session.commit()
    collect_system_metrics(db_session, now=now + timedelta(minutes=1))
    aggregate = db_session.query(MetricAggregate).one()
    assert aggregate.sample_count == 2
    assert aggregate.data["averages"]["outbox_pending"] == 0.5


def test_presence_retention_respects_batch_limit(db_session, seed_admin, seed_royal, seed_mechanic):
    from robopark_api.services.system_observability import UserPresence, prune_observability

    now = datetime(2026, 9, 24, tzinfo=UTC)
    for user in (seed_admin, seed_royal, seed_mechanic):
        db_session.add(UserPresence(user_id=user.id, last_seen_at=now - timedelta(days=8)))
    db_session.commit()
    prune_observability(db_session, now=now, limit=2)
    assert db_session.query(UserPresence).count() == 1


def test_metric_collector_retries_after_transient_database_failure(db_engine, monkeypatch):
    from robopark_api.services import system_observability

    original = system_observability.collect_system_metrics
    calls = 0
    collected = threading.Event()

    def flaky(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database failure")
        original(db, **kwargs)
        collected.set()

    monkeypatch.setattr(system_observability, "collect_system_metrics", flaky)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(
            system_observability.run_metric_collection_loop(
                sessionmaker(bind=db_engine), stop, interval_seconds=0.01
            )
        )
        assert await asyncio.to_thread(collected.wait, 1)
        stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(run())
    assert calls == 2


def test_summary_exposes_payload_free_worker_tracker_outbox_and_push_health(
    client, db_session, seed_royal, test_settings
):
    from robopark_api.services.system_observability import collect_system_metrics

    collect_system_metrics(db_session, settings=test_settings)
    login_as(client, "royal", "secret")
    body = client.get("/admin/system/summary").json()
    assert body["sync"]["worker_lease_state"] in {"unknown", "active", "stale"}
    assert "cursor_age_seconds" in body["sync"]
    assert "pending_action_count" in body["sync"]
    assert body["push"] == {"pending": 0, "needs_attention": 0}
    assert "disk" in body["metrics"]["host"]
    assert "requests" in body["metrics"]["host"]
    assert "version" in body["release"]


def test_admin_presence_excludes_other_park(
    client, db_session, seed_admin, seed_mechanic, seed_park_with_tracker
):
    db_session.add(UserPark(user_id=seed_admin.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    login_as(client, "mech1", "secret")
    assert client.post("/presence/heartbeat").status_code == 204
    login_as(client, "admin", "secret")
    body = client.get("/admin/system/summary").json()
    assert body["online"]["total"] == 1
    assert body["online"]["by_role"] == {"mechanic": 1}
