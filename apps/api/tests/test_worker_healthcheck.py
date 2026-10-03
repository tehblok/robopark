from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from robopark_api.schedule_models import TrackerNotificationCursor
from robopark_api.services.sync_health import record_worker_heartbeat
from robopark_api.services.system_observability import MetricRaw


def _healthcheck_module():
    spec = importlib.util.find_spec("robopark_api.worker_healthcheck")
    assert spec is not None, "worker healthcheck module is missing"
    return importlib.import_module("robopark_api.worker_healthcheck")


def test_worker_healthcheck_starts_without_importing_the_full_orm(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "src"
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import robopark_api.worker_healthcheck; import sys; "
            "assert 'sqlalchemy' not in sys.modules",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_postgres_probe_reads_heartbeat_and_metric_without_orm(monkeypatch):
    healthcheck = _healthcheck_module()
    now = datetime.now(UTC)
    commands = []
    connection_options = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, command, params=None):
            commands.append((command, params))

        def fetchone(self):
            if len(commands) == 1:
                return (now + timedelta(seconds=30), (now - timedelta(seconds=5)).isoformat())
            return (now - timedelta(seconds=2),)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return Cursor()

    def connect(dsn, **options):
        connection_options.append((dsn, options))
        return Connection()

    monkeypatch.setattr("psycopg.connect", connect)

    result = healthcheck._postgres_healthcheck(
        "postgresql+psycopg://robopark@db:5432/robopark", max_age_seconds=120
    )

    assert result.reason == "worker_healthy"
    assert connection_options == [
        (
            "postgresql://robopark@db:5432/robopark",
            {"connect_timeout": 3, "options": "-c statement_timeout=3000"},
        )
    ]
    assert len(commands) == 2
    assert commands[0][1] == ("worker-runtime",)


def test_worker_healthcheck_requires_active_heartbeat_and_fresh_metric(db_session):
    healthcheck = _healthcheck_module()
    now = datetime.now(UTC)
    db_session.add(
        TrackerNotificationCursor(
            scope_key="worker-runtime",
            lease_owner="worker-a",
            lease_until=now + timedelta(seconds=30),
            last_success_at=now - timedelta(seconds=20),
            cursor_value=(now - timedelta(seconds=20)).isoformat(),
        )
    )
    db_session.add(MetricRaw(sampled_at=now - timedelta(seconds=10), data={"sync": {}}))
    db_session.commit()

    result = healthcheck.assess_worker_health(db_session, now=now, max_age_seconds=60)

    assert result.healthy is True
    assert result.reason == "worker_healthy"


def test_worker_healthcheck_reports_stable_reason_for_missing_metric(db_session):
    healthcheck = _healthcheck_module()
    now = datetime.now(UTC)
    db_session.add(
        TrackerNotificationCursor(
            scope_key="worker-runtime",
            lease_owner="worker-a",
            lease_until=now + timedelta(seconds=30),
            last_success_at=now,
            cursor_value=now.isoformat(),
        )
    )
    db_session.commit()

    result = healthcheck.assess_worker_health(db_session, now=now, max_age_seconds=60)

    assert result.healthy is False
    assert result.reason == "worker_metric_missing"


def test_worker_healthcheck_rejects_metric_from_before_current_worker(db_session):
    healthcheck = _healthcheck_module()
    now = datetime.now(UTC)
    db_session.add(
        TrackerNotificationCursor(
            scope_key="worker-runtime",
            lease_owner="worker-b",
            lease_until=now + timedelta(seconds=30),
            last_success_at=now,
            cursor_value=now.isoformat(),
        )
    )
    db_session.add(MetricRaw(sampled_at=now - timedelta(seconds=10), data={"sync": {}}))
    db_session.commit()

    result = healthcheck.assess_worker_health(db_session, now=now, max_age_seconds=60)

    assert result.healthy is False
    assert result.reason == "worker_metric_missing"


def test_worker_healthcheck_rejects_stale_heartbeat_before_stale_metric(db_session):
    healthcheck = _healthcheck_module()
    now = datetime.now(UTC)
    db_session.add(
        TrackerNotificationCursor(
            scope_key="worker-runtime",
            lease_owner="worker-a",
            lease_until=now - timedelta(seconds=1),
            last_success_at=now - timedelta(seconds=120),
            cursor_value=(now - timedelta(seconds=120)).isoformat(),
        )
    )
    db_session.add(MetricRaw(sampled_at=now - timedelta(seconds=120), data={"sync": {}}))
    db_session.commit()

    result = healthcheck.assess_worker_health(db_session, now=now, max_age_seconds=60)

    assert result.healthy is False
    assert result.reason == "worker_heartbeat_stale"


def test_worker_healthcheck_rejects_metric_far_ahead_of_api_clock(db_session):
    healthcheck = _healthcheck_module()
    now = datetime.now(UTC)
    db_session.add(
        TrackerNotificationCursor(
            scope_key="worker-runtime",
            lease_owner="worker-a",
            lease_until=now + timedelta(seconds=30),
            last_success_at=now,
            cursor_value=(now - timedelta(seconds=20)).isoformat(),
        )
    )
    db_session.add(MetricRaw(sampled_at=now + timedelta(minutes=10), data={"sync": {}}))
    db_session.commit()

    result = healthcheck.assess_worker_health(db_session, now=now, max_age_seconds=60)

    assert result.healthy is False
    assert result.reason == "worker_metric_future"


def test_worker_start_timestamp_survives_heartbeat_refresh(db_session):
    started_at = datetime.now(UTC)
    record_worker_heartbeat(db_session, owner_id="worker-a", now=started_at)
    record_worker_heartbeat(db_session, owner_id="worker-a", now=started_at + timedelta(seconds=15))

    heartbeat = db_session.get(TrackerNotificationCursor, "worker-runtime")

    assert heartbeat is not None
    assert heartbeat.cursor_value == started_at.isoformat()
