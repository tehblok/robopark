import asyncio
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from conftest import login_as
from robopark_api.models import AccessStatus, Permission, Role, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.task_workflow_models import ReliableAction


def test_presence_is_database_backed_and_expires_after_two_minutes(
    client, db_session, seed_royal, seed_mechanic, seed_park_with_tracker
):
    login_as(client, "mech1", "secret")
    assert client.post("/presence/heartbeat", json={"timezone": "Europe/Moscow"}).status_code == 204
    db_session.refresh(seed_mechanic)
    assert seed_mechanic.timezone == "Europe/Moscow"
    assert client.post("/presence/heartbeat", json={"timezone": "not/a-zone"}).status_code == 422
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


def test_heartbeat_retries_conflicting_insert_and_never_moves_presence_backwards(
    db_engine, db_session, seed_mechanic
):
    from robopark_api.services.system_observability import PresenceSample, UserPresence, heartbeat

    later = datetime(2026, 9, 24, 12, 1, tzinfo=UTC)
    with sessionmaker(bind=db_engine)() as first, sessionmaker(bind=db_engine)() as second:
        heartbeat(first, seed_mechanic, now=later)
        heartbeat(second, seed_mechanic, now=later - timedelta(seconds=30))
    db_session.expire_all()
    assert db_session.get(UserPresence, seed_mechanic.id).last_seen_at.replace(tzinfo=UTC) == later
    assert db_session.query(PresenceSample).filter_by(user_id=seed_mechanic.id).count() == 1


def test_admin_system_history_requires_admin_and_bounds_days(client, seed_mechanic, seed_royal):
    login_as(client, "mech1", "secret")
    assert client.get("/admin/system/history").status_code == 403
    login_as(client, "royal", "secret")
    assert client.get("/admin/system/history?days=8").status_code == 422
    response = client.get("/admin/system/history?days=7")
    assert response.status_code == 200
    assert set(response.json()) >= {"active_users", "metrics"}


def test_custom_role_with_nav_admin_cannot_read_system_telemetry(client, db_session):
    permission = db_session.scalar(select(Permission).where(Permission.key == rbac.PERMISSION_NAV_ADMIN))
    role = Role(slug="system-viewer", name="System viewer", is_system=False, permissions=[permission])
    db_session.add(role)
    db_session.flush()
    db_session.add(User(
        username="system-viewer", password_hash=hash_password("secret"), role_id=role.id,
        access_status=AccessStatus.approved.value, is_active=True,
    ))
    db_session.commit()
    login_as(client, "system-viewer", "secret")
    assert client.get("/admin/system/summary").status_code == 403
    assert client.get("/admin/system/history").status_code == 403


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


def test_summary_reports_metric_sample_time_and_staleness(client, db_session, seed_royal):
    from robopark_api.services.system_observability import MetricRaw

    sampled = datetime.now(UTC) - timedelta(hours=1)
    db_session.add(MetricRaw(sampled_at=sampled, data={"sync": {"pending_action_count": 0}}))
    db_session.commit()
    login_as(client, "royal", "secret")
    body = client.get("/admin/system/summary").json()
    assert datetime.fromisoformat(body["sampled_at"]) == sampled
    assert body["metrics_stale"] is True


def test_active_user_history_obeys_requested_day_window(db_session, seed_royal, seed_mechanic):
    from robopark_api.services.system_observability import PresenceSample, active_user_history

    now = datetime.now(UTC)
    db_session.add(PresenceSample(user_id=seed_mechanic.id, bucket_at=now - timedelta(days=6)))
    db_session.commit()
    assert active_user_history(db_session, actor=seed_royal, now=now, days=1) == []
    assert len(active_user_history(db_session, actor=seed_royal, now=now, days=7)) == 1


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
    for field in ("cpu", "postgresql", "container", "tuna", "internet"):
        assert body["metrics"]["host"][field]["state"] in {"ok", "degraded", "unknown"}
    assert "version" in body["release"]


def test_metric_collection_consumes_fresh_host_service_projection(
    client, db_session, seed_royal, test_settings, tmp_path
):
    import json

    from robopark_api.services.system_observability import collect_system_metrics

    public = tmp_path / "host-health.json"
    public.write_text(
        json.dumps(
            {
                "services_checked_at": datetime.now(UTC).isoformat(),
                "services": {"docker": "ok", "tuna": "degraded", "internet": "ok"},
            }
        )
    )
    settings = test_settings.model_copy(update={"host_health_path": str(public)})
    collect_system_metrics(db_session, settings=settings)
    login_as(client, "royal", "secret")
    host = client.get("/admin/system/summary").json()["metrics"]["host"]
    assert host["container"]["state"] == "ok"
    assert host["tuna"]["state"] == "degraded"
    assert host["internet"]["state"] == "ok"


def test_stale_host_service_projection_is_explicitly_unknown(tmp_path):
    import json

    from robopark_api.services.operational_health import cached_host_snapshot

    public = tmp_path / "host-health.json"
    public.write_text(
        json.dumps(
            {
                "services_checked_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "services": {"docker": "ok", "tuna": "ok", "internet": "ok"},
            }
        )
    )
    host = cached_host_snapshot(tmp_path, tmp_path, public)
    assert host["container"]["state"] == "unknown"
    assert host["tuna"]["state"] == "unknown"
    assert host["internet"]["state"] == "unknown"


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
