"""Scoped system telemetry and authenticated online heartbeat."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.crypto import SecretDecryptionError
from robopark_api.db import get_db
from robopark_api.deps import require_builtin_admin_or_royal, require_user
from robopark_api.models import User
from robopark_api.ops_schemas import ReleaseStatusOut
from robopark_api.schedule_models import TrackerNotificationCursor
from robopark_api.services import platform_settings
from robopark_api.services.ops import host_bridge
from robopark_api.services.ops.context import resolved_ops_dir
from robopark_api.services.release_status import release_status
from robopark_api.services.sync_health import WORKER_SCOPE, sync_health
from robopark_api.services.system_observability import (
    MetricRaw,
    active_user_history,
    heartbeat,
    metric_history,
    online_counts,
    push_health,
)
from robopark_api.worker_healthcheck import MAX_METRIC_FUTURE_SKEW, assess_worker_sample

router = APIRouter(tags=["system"])


class PresenceHeartbeatInput(BaseModel):
    timezone: str

    @field_validator("timezone")
    @classmethod
    def valid_iana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("invalid IANA timezone") from exc
        return value


@router.post("/presence/heartbeat", status_code=204)
def presence_heartbeat(
    payload: PresenceHeartbeatInput | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> None:
    heartbeat(db, user, timezone=payload.timezone if payload else None)


@router.get("/admin/system/summary")
def system_summary(
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_builtin_admin_or_royal),
    settings: Settings = Depends(get_settings),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    if settings.ops_host_root:
        try:
            release = release_status(host_bridge.host_root(settings))
        except host_bridge.BridgeError:
            # A configured but unavailable host bridge cannot be replaced by the
            # API container's local metadata: it may describe a different build.
            release = ReleaseStatusOut()
    else:
        release = release_status(resolved_ops_dir(settings))
    now = datetime.now(UTC)
    latest = db.scalar(
        select(MetricRaw).order_by(MetricRaw.sampled_at.desc(), MetricRaw.id.desc()).limit(1)
    )
    sampled_at = latest.sampled_at if latest is not None else None
    if sampled_at is not None:
        sampled_at = (
            sampled_at.replace(tzinfo=UTC)
            if sampled_at.tzinfo is None
            else sampled_at.astimezone(UTC)
        )
    sync = sync_health(db, now=now, resource_type="tracker_issue")
    worker_health = assess_worker_sample(
        db.get(TrackerNotificationCursor, WORKER_SCOPE),
        latest.sampled_at if latest is not None else None,
        now=now,
    )
    try:
        stored_tracker = platform_settings.get_setting(db, platform_settings.TRACKER_TOKEN_KEY)
        tracker_token = (
            platform_settings.get_tracker_token(db) if stored_tracker is not None else None
        )
        tracker_state = (
            "configured"
            if tracker_token
            else (
                "credential_unavailable"
                if stored_tracker is not None and stored_tracker.value
                else "not_configured"
            )
        )
    except SecretDecryptionError:
        tracker_state = "credential_unavailable"
    return {
        "sampled_at": sampled_at.isoformat() if sampled_at is not None else None,
        "metrics_stale": (
            sampled_at is None
            or now - sampled_at > timedelta(minutes=2)
            or sampled_at > now + MAX_METRIC_FUTURE_SKEW
        ),
        "online": online_counts(db, actor=actor),
        "sync": sync.model_dump(mode="json"),
        "tracker": {"state": tracker_state},
        "worker_health": worker_health.reason,
        "push": push_health(db),
        "metrics": latest.data if latest is not None else None,
        "release": release.model_dump(mode="json"),
    }


@router.get("/admin/system/history")
def system_history(
    response: Response,
    days: int = Query(default=7, ge=1, le=7),
    db: Session = Depends(get_db),
    actor: User = Depends(require_builtin_admin_or_royal),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {
        "active_users": active_user_history(db, actor=actor, days=days),
        "metrics": metric_history(db, days=days),
    }
