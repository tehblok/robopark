from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_dashboard_park, require_user
from robopark_api.models import User
from robopark_api.schemas import (
    DashboardHistoryOut,
    DashboardHistoryPointOut,
    DashboardMovingItemOut,
    DashboardSummaryOut,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_cache, tracker_client, tracker_filters
from robopark_api.services.blocker_history import history_series

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

MOVING_LIST_LIMIT = 20


def _fetch_moving_items(
    *,
    token: str,
    queue: str,
    park_tag: str,
    priority: str = "blocker",
    issue_type: str | None = None,
) -> list[DashboardMovingItemOut]:
    issues = tracker_cache.fetch_park_blockers(
        token=token,
        queue=queue,
        park_tag=park_tag,
        priority=priority,
        issue_type=issue_type,
    )
    sorted_issues = tracker_filters.sort_issues_oldest_first(issues)
    moving = tracker_filters.filter_issues_by_status(sorted_issues, "moving")
    return [
        DashboardMovingItemOut(key=issue["key"], summary=issue["summary"])
        for issue in moving[:MOVING_LIST_LIMIT]
    ]


@router.get("/summary", response_model=DashboardSummaryOut)
def dashboard_summary(
    park_id: int = Query(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> DashboardSummaryOut:
    park = require_dashboard_park(park_id, db, user)

    queue = (park.tracker_queue or "").strip()
    if not queue:
        return DashboardSummaryOut(
            park_id=park.id,
            arrived=0,
            done=0,
            queued=0,
            in_transit=0,
            moving=[],
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    try:
        priority, issue_type = tracker_filters.park_priority_type(park)
        metrics = tracker_cache.collect_park_metrics(
            token=token,
            queue=queue,
            tag=park.tag,
            priority=priority,
            issue_type=issue_type,
        )
        moving = _fetch_moving_items(
            token=token,
            queue=queue,
            park_tag=park.tag,
            priority=priority,
            issue_type=issue_type,
        )
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc

    return DashboardSummaryOut(
        park_id=park.id,
        arrived=int(metrics.get("arrived") or 0),
        done=int(metrics.get("done") or 0),
        queued=int(metrics.get("queued") or 0),
        in_transit=int(metrics.get("in_transit") or 0),
        moving=moving,
    )


@router.get("/history", response_model=DashboardHistoryOut)
def dashboard_history(
    park_id: int = Query(...),
    days: int = Query(default=7, ge=1, le=30),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> DashboardHistoryOut:
    require_dashboard_park(park_id, db, user)
    rows = history_series(db, park_id=park_id, days=days)
    return DashboardHistoryOut(
        park_id=park_id,
        points=[DashboardHistoryPointOut(**row) for row in rows],
    )
