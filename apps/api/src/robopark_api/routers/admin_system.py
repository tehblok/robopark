"""Scoped system telemetry and authenticated online heartbeat."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_admin, require_user
from robopark_api.models import User
from robopark_api.services.ops.context import resolved_ops_dir
from robopark_api.services.release_status import release_status
from robopark_api.services.sync_health import sync_health
from robopark_api.services.system_observability import (
    MetricRaw,
    active_user_history,
    heartbeat,
    metric_history,
    online_counts,
    push_health,
)

router = APIRouter(tags=["system"])


@router.post("/presence/heartbeat", status_code=204)
def presence_heartbeat(db: Session = Depends(get_db), user: User = Depends(require_user)) -> None:
    heartbeat(db, user)


@router.get("/admin/system/summary")
def system_summary(
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    latest = db.scalar(
        select(MetricRaw).order_by(MetricRaw.sampled_at.desc(), MetricRaw.id.desc()).limit(1)
    )
    return {
        "sampled_at": datetime.now(UTC).isoformat(),
        "online": online_counts(db, actor=actor),
        "sync": sync_health(db).model_dump(mode="json"),
        "push": push_health(db),
        "metrics": latest.data if latest is not None else None,
        "release": release_status(resolved_ops_dir(settings)).model_dump(mode="json"),
    }


@router.get("/admin/system/history")
def system_history(
    response: Response,
    days: int = Query(default=7, ge=1, le=7),
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {
        "active_users": active_user_history(db, actor=actor),
        "metrics": metric_history(db, days=days),
    }
