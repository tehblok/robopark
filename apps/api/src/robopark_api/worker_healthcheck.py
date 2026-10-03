"""Container health probe for the dedicated background worker.

The API readiness endpoint intentionally does not depend on background jobs.
This probe verifies the worker's separate database-backed contract: its lease
must still be active and it must have produced a recent metrics sample.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

MAX_METRIC_FUTURE_SKEW = timedelta(seconds=60)
WORKER_SCOPE = "worker-runtime"


@dataclass(frozen=True)
class WorkerHealthcheckResult:
    healthy: bool
    reason: str


class HeartbeatSample(Protocol):
    lease_until: datetime | None
    cursor_value: str | None


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _worker_started_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except ValueError:
        return None


def assess_worker_health(
    db: Session,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 120,
) -> WorkerHealthcheckResult:
    """Return a stable, payload-free reason suitable for Docker diagnostics."""
    if max_age_seconds < 1:
        raise ValueError("invalid_max_age_seconds")
    from sqlalchemy import select

    from robopark_api.schedule_models import TrackerNotificationCursor
    from robopark_api.services.system_observability import MetricRaw

    current = now or datetime.now(UTC)
    heartbeat = db.get(TrackerNotificationCursor, WORKER_SCOPE)
    heartbeat_health = assess_worker_sample(heartbeat, None, now=current)
    if heartbeat_health.reason != "worker_metric_missing":
        return heartbeat_health
    sampled_at = db.scalar(
        select(MetricRaw.sampled_at).order_by(MetricRaw.sampled_at.desc()).limit(1)
    )
    return assess_worker_sample(heartbeat, sampled_at, now=current, max_age_seconds=max_age_seconds)


def assess_worker_sample(
    heartbeat: HeartbeatSample | None,
    sampled_at: datetime | None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 120,
) -> WorkerHealthcheckResult:
    """Apply the Docker health contract to a snapshot already read by the API."""
    if max_age_seconds < 1:
        raise ValueError("invalid_max_age_seconds")
    current = _aware(now or datetime.now(UTC))
    if heartbeat is None or heartbeat.lease_until is None:
        return WorkerHealthcheckResult(False, "worker_heartbeat_missing")
    if _aware(heartbeat.lease_until) <= current:
        return WorkerHealthcheckResult(False, "worker_heartbeat_stale")
    if sampled_at is None:
        return WorkerHealthcheckResult(False, "worker_metric_missing")
    worker_started_at = _worker_started_at(heartbeat.cursor_value)
    if worker_started_at is None or _aware(sampled_at) < worker_started_at:
        return WorkerHealthcheckResult(False, "worker_metric_missing")
    if _aware(sampled_at) > current + MAX_METRIC_FUTURE_SKEW:
        return WorkerHealthcheckResult(False, "worker_metric_future")
    if _aware(sampled_at) < current - timedelta(seconds=max_age_seconds):
        return WorkerHealthcheckResult(False, "worker_metric_stale")
    return WorkerHealthcheckResult(True, "worker_healthy")


@dataclass(frozen=True)
class _Heartbeat:
    lease_until: datetime | None
    cursor_value: str | None


def _postgres_healthcheck(dsn: str, *, max_age_seconds: int) -> WorkerHealthcheckResult:
    """Probe two small rows without importing the API's complete ORM graph."""
    import psycopg

    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn.partition("://")[2]
    with (
        psycopg.connect(dsn, connect_timeout=3, options="-c statement_timeout=3000") as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT lease_until, cursor_value FROM tracker_notification_cursors "
            "WHERE scope_key = %s",
            (WORKER_SCOPE,),
        )
        row = cursor.fetchone()
        if row is None:
            return WorkerHealthcheckResult(False, "worker_heartbeat_missing")
        cursor.execute("SELECT sampled_at FROM system_metric_raw ORDER BY sampled_at DESC LIMIT 1")
        sample = cursor.fetchone()
    heartbeat = _Heartbeat(lease_until=row[0], cursor_value=row[1])
    return assess_worker_sample(
        heartbeat, sample[0] if sample else None, max_age_seconds=max_age_seconds
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--max-age-seconds", type=int, default=120)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        dsn = os.environ.get("DATABASE_URL", "")
        if dsn.startswith(("postgresql+psycopg://", "postgresql://")):
            result = _postgres_healthcheck(dsn, max_age_seconds=arguments.max_age_seconds)
        else:
            from robopark_api.db import SessionLocal

            with SessionLocal() as db:
                result = assess_worker_health(db, max_age_seconds=arguments.max_age_seconds)
    except Exception as error:  # noqa: BLE001 - health output must stay finite and secret-free.
        print(f"worker_healthcheck_error:{type(error).__name__}")
        return 1
    print(result.reason)
    return 0 if result.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
