import json
import time
from datetime import UTC, datetime, timedelta

from conftest import login_as
from robopark_api.schedule_models import TrackerNotificationCursor
from robopark_api.services import sync_health
from robopark_api.task_workflow_models import ReliableAction


def test_sync_health_reads_shared_heartbeat_and_finite_queue_projection(
    db_session, seed_mechanic
):
    now = datetime.now(UTC)
    db_session.add(
        TrackerNotificationCursor(
            scope_key="new-tasks",
            cursor_value=json.dumps(["2026-09-20T18:00:00+00:00", "ROBOPARK-1"]),
            last_success_at=now - timedelta(seconds=23),
            last_error="tracker_unavailable",
        )
    )
    db_session.add(
        ReliableAction(
            actor_user_id=seed_mechanic.id,
            resource_type="tracker_issue",
            resource_id="ROBOPARK-1",
            action="comment",
            idempotency_key="health-test",
            payload_hash="x" * 64,
            payload_json='{"secret":"must-never-leak"}',
            state="retry_wait",
            attempts=2,
            next_attempt_at=0,
            created_at=time.time() - 100,
            updated_at=time.time(),
        )
    )
    db_session.commit()
    sync_health.record_worker_heartbeat(db_session, owner_id="worker-a", now=now)

    result = sync_health.sync_health(db_session, now=now).model_dump(mode="json")

    assert result["cursor_age_seconds"] == 23
    assert 99 <= result["oldest_pending_action_age_seconds"] <= 101
    assert result["retry_count"] == 1
    assert result["last_error"] == "tracker_unavailable"
    assert result["worker_lease_state"] == "active"
    assert "must-never-leak" not in json.dumps(result)
    assert "worker-a" not in json.dumps(result)


def test_admin_and_royal_can_read_sync_health_but_mechanic_cannot(
    client, db_session, seed_mechanic, seed_admin, seed_royal, monkeypatch
):
    from robopark_api.routers import admin_health

    monkeypatch.setattr(admin_health, "cached_host_snapshot", lambda *_: {})
    assert client.get("/admin/health").status_code == 401
    login_as(client, seed_mechanic.username, "secret")
    assert client.get("/admin/health").status_code == 403
    for actor in (seed_admin, seed_royal):
        login_as(client, actor.username, "secret")
        response = client.get("/admin/health")
        assert response.status_code == 200
        assert response.json()["sync"]["worker_lease_state"] == "unknown"
        assert response.headers["cache-control"] == "no-store"
