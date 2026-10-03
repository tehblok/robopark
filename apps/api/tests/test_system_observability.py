import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import Response
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
    permission = db_session.scalar(
        select(Permission).where(Permission.key == rbac.PERMISSION_NAV_ADMIN)
    )
    role = Role(
        slug="system-viewer", name="System viewer", is_system=False, permissions=[permission]
    )
    db_session.add(role)
    db_session.flush()
    db_session.add(
        User(
            username="system-viewer",
            password_hash=hash_password("secret"),
            role_id=role.id,
            access_status=AccessStatus.approved.value,
            is_active=True,
        )
    )
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


def test_summary_reads_release_metadata_from_installed_host_projection(
    client, seed_royal, test_settings, tmp_path
):
    host = tmp_path / "host-ops"
    for name in ("inbox", "artifacts", "public"):
        (host / name).mkdir(parents=True)
    object.__setattr__(test_settings, "ops_host_root", str(host))
    (host / "public/release-status.json").write_text(
        json.dumps(
            {
                "version": "0.2.0-rc.15.dev1",
                "build_id": "0123456789abcdef",
                "git_sha": "a" * 40,
                "database_head": "0054_park_coordinates",
                "channel": "manual",
            }
        )
    )
    local_public = Path(test_settings.ops_dir) / "public"
    local_public.mkdir(parents=True)
    (local_public / "release-status.json").write_text("{}")
    login_as(client, "royal", "secret")

    release = client.get("/admin/system/summary").json()["release"]

    assert release["version"] == "0.2.0-rc.15.dev1"
    assert release["build_id"] == "0123456789abcdef"
    assert release["git_sha"] == "a" * 40
    assert release["database_head"] == "0054_park_coordinates"


def test_summary_keeps_metrics_and_rejects_local_release_when_host_bridge_is_unavailable(
    db_session, seed_royal, test_settings, tmp_path
):
    from robopark_api.routers.admin_system import system_summary

    object.__setattr__(test_settings, "ops_host_root", str(tmp_path / "missing-host-ops"))
    local_public = Path(test_settings.ops_dir) / "public"
    local_public.mkdir(parents=True)
    (local_public / "release-status.json").write_text(
        json.dumps({"version": "9.9.9", "build_id": "f" * 16})
    )
    body = system_summary(Response(), db_session, seed_royal, test_settings)

    assert body["release"]["version"] is None
    assert body["release"]["build_id"] is None
    assert "online" in body
    assert "sync" in body


@pytest.mark.parametrize(
    ("age_seconds", "expected_stale"),
    [
        (10, False),
        (3600, True),
        (-600, True),
    ],
)
def test_summary_normalizes_aware_metric_time_before_staleness_check(
    db_session, seed_royal, test_settings, monkeypatch, age_seconds, expected_stale
):
    from robopark_api.routers.admin_system import system_summary
    from robopark_api.services.system_observability import MetricRaw

    sampled = (datetime.now(UTC) - timedelta(seconds=age_seconds)).astimezone(
        timezone(timedelta(hours=3))
    )
    original_scalar = db_session.scalar

    def scalar(statement, *args, **kwargs):
        if statement.column_descriptions[0]["expr"] is MetricRaw:
            return MetricRaw(sampled_at=sampled, data={"host": {}})
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", scalar)

    body = system_summary(Response(), db_session, seed_royal, test_settings)

    assert datetime.fromisoformat(body["sampled_at"]) == sampled
    assert body["metrics_stale"] is expected_stale


def test_summary_exposes_worker_metric_failure_despite_active_heartbeat(
    client, db_session, seed_royal
):
    from robopark_api.services.sync_health import record_worker_heartbeat
    from robopark_api.services.system_observability import MetricRaw

    now = datetime.now(UTC)
    record_worker_heartbeat(db_session, owner_id="worker-a", now=now)
    db_session.add(MetricRaw(sampled_at=now - timedelta(seconds=10), data={"host": {}}))
    db_session.commit()
    login_as(client, "royal", "secret")

    body = client.get("/admin/system/summary").json()

    assert body["sync"]["worker_lease_state"] == "active"
    assert body["worker_health"] == "worker_metric_missing"


def test_system_attention_metric_counts_only_tracker_issues_linked_by_the_console(
    client,
    db_session,
    seed_royal,
):
    for index, resource_type in enumerate(("schedule_park", "task_control", "tracker_issue")):
        db_session.add(
            ReliableAction(
                id=f"attention-{index}",
                actor_user_id=seed_royal.id,
                resource_type=resource_type,
                resource_id=f"resource-{index}",
                action="sync",
                idempotency_key=f"attention-key-{index}",
                payload_hash=str(index) * 64,
                payload_json="{}",
                state="needs_attention",
                next_attempt_at=0,
                created_at=1.0 + index,
                updated_at=1.0 + index,
            )
        )
    db_session.commit()
    login_as(client, "royal", "secret")

    body = client.get("/admin/system/summary").json()

    assert body["sync"]["pending_action_count"] == 1
    assert body["sync"]["needs_attention_count"] == 1


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
    assert body["tracker"] == {"state": "not_configured"}
    assert "disk" in body["metrics"]["host"]
    assert "requests" in body["metrics"]["host"]
    for field in ("cpu", "postgresql", "container", "tuna", "internet", "wifi"):
        assert body["metrics"]["host"][field]["state"] in {"ok", "degraded", "unknown"}
    assert "version" in body["release"]


def test_system_summary_distinguishes_unreadable_tracker_credential(
    db_session, seed_royal, test_settings
):
    import secrets

    from fastapi import Response

    from robopark_api.models import PlatformSetting
    from robopark_api.routers.admin_system import system_summary
    from robopark_api.services.platform_settings import TRACKER_TOKEN_KEY

    db_session.add(
        PlatformSetting(key=TRACKER_TOKEN_KEY, value=f"enc:v1:{secrets.token_urlsafe(24)}")
    )
    db_session.commit()

    body = system_summary(Response(), db_session, seed_royal, test_settings)
    assert body["tracker"] == {"state": "credential_unavailable"}


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
                "services": {
                    "docker": "ok",
                    "tuna": "degraded",
                    "internet": "ok",
                    "wifi": "degraded",
                },
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
    assert host["wifi"]["state"] == "degraded"


def test_metric_collection_survives_malformed_host_capabilities(
    db_session, test_settings, tmp_path
):
    import json

    from robopark_api.services.system_observability import MetricRaw, collect_system_metrics

    public = tmp_path / "host-health.json"
    public.write_text(json.dumps({"capabilities": None}))
    settings = test_settings.model_copy(update={"host_health_path": str(public)})

    sample = collect_system_metrics(db_session, settings=settings)

    assert sample["host"]["capabilities"]["profile"] == "generic-arm"
    assert sample["host"]["capabilities"]["source_state"] == "invalid"
    assert db_session.query(MetricRaw).count() == 1


def test_stale_host_capabilities_are_not_reported_as_current(tmp_path):
    import json
    import time

    from robopark_api.services.operational_health import cached_host_snapshot

    public = tmp_path / "host-health.json"
    public.write_text(
        json.dumps(
            {
                "capabilities": {
                    "profile": "orin",
                    "jpeg_backend": "software",
                    "checked_at": time.time() - 3600,
                }
            }
        )
    )

    capabilities = cached_host_snapshot(tmp_path, tmp_path, public)["capabilities"]

    assert capabilities["source_state"] == "stale"


def test_stale_host_service_projection_is_explicitly_unknown(tmp_path):
    import json

    from robopark_api.services.operational_health import cached_host_snapshot

    public = tmp_path / "host-health.json"
    public.write_text(
        json.dumps(
            {
                "services_checked_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "services": {"docker": "ok", "tuna": "ok", "internet": "ok", "wifi": "ok"},
            }
        )
    )
    host = cached_host_snapshot(tmp_path, tmp_path, public)
    assert host["container"]["state"] == "unknown"
    assert host["tuna"]["state"] == "unknown"
    assert host["internet"]["state"] == "unknown"
    assert host["wifi"]["state"] == "unknown"


def test_host_snapshot_exposes_failed_disk_measurement_and_unreadable_agent_snapshot(
    tmp_path, monkeypatch
):
    from robopark_api.services import operational_health

    def unavailable(_path):
        raise OSError("not mounted")

    monkeypatch.setattr(operational_health.shutil, "disk_usage", unavailable)
    host = operational_health.cached_host_snapshot(
        tmp_path / "missing-data", tmp_path, tmp_path / "missing-host-health.json"
    )

    assert host["disk"]["source_state"] == "unavailable"
    assert host["disk"]["total_bytes"] is None
    assert host["host_health_source_state"] == "unavailable"


def test_host_snapshot_exposes_failed_builder_budget_without_trusting_error_text(tmp_path):
    import json
    import time

    from robopark_api.services.operational_health import cached_host_snapshot

    public = tmp_path / "host-health.json"
    public.write_text(
        json.dumps(
            {
                "builder_cache_budget": {
                    "attempted": True,
                    "blocked": True,
                    "checked_at": time.time(),
                    "error": "private docker error",
                }
            }
        )
    )

    storage = cached_host_snapshot(tmp_path, tmp_path, public)["storage"]

    assert storage["builder_cache_budget"] == {"attempted": True, "blocked": True}


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
