from bisect import bisect_right
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from robopark_api.analytics_schemas import AnalyticsBucket, AnalyticsOut, AnalyticsTaskKeysPage
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.services import analytics, rbac
from robopark_api.services.operations import ROLE_STATUSES, require_operations_park

router = APIRouter(prefix="/analytics", tags=["analytics"])
TASK_KEYS_PAGE_SIZE = 50
TaskKeysGroup = Literal["all", "closures", "series", "age", "sla", "stage", "workload"]


def _authorized_analytics(
    db: Session, user: User, park_id: int, days: int, bucket: AnalyticsBucket, *, now=None
) -> AnalyticsOut:
    rbac.require_approved_permission(db, user, rbac.PERMISSION_NAV_ANALYTICS)
    rbac.require_approved_permission(db, user, rbac.PERMISSION_TRACKER_READ)
    park = require_operations_park(db, user, park_id)
    return analytics.build_analytics(
        db,
        park_id=park.id,
        days=days,
        bucket=bucket,
        now=now,
        allowed_statuses=ROLE_STATUSES.get(user.role),
    )


def _task_keys(report: AnalyticsOut, group: TaskKeysGroup, key: str | None) -> list[str]:
    if group == "all":
        return report.drilldown_task_keys
    if group == "closures":
        return report.verified_closures.task_keys
    if group == "sla":
        return report.sla_trend.task_keys
    if key is None:
        raise HTTPException(400, "analytics_task_keys_metric_required")
    if group == "series":
        metric = report.series.get(key)
    else:
        metrics = {
            "age": report.backlog_age_bands,
            "stage": report.stage_durations,
            "workload": report.workload,
        }[group]
        metric = next((item for item in metrics if item.key == key), None)
    if metric is None:
        raise HTTPException(404, "analytics_task_keys_metric_not_found")
    return metric.task_keys


def _bound_task_keys(report: AnalyticsOut) -> AnalyticsOut:
    report.drilldown_task_keys_count = len(report.drilldown_task_keys)
    report.drilldown_task_keys = report.drilldown_task_keys[:TASK_KEYS_PAGE_SIZE]
    metrics = [
        *report.series.values(),
        *report.backlog_age_bands,
        report.sla_trend,
        *report.stage_durations,
        *report.workload,
    ]
    for metric in metrics:
        metric.task_keys_count = len(metric.task_keys)
        metric.task_keys = metric.task_keys[:TASK_KEYS_PAGE_SIZE]
    report.verified_closures.task_keys = report.verified_closures.task_keys[:TASK_KEYS_PAGE_SIZE]
    return report


@router.get("", response_model=AnalyticsOut)
def historical_analytics(
    park_id: int = Query(..., ge=1),
    days: int = Query(7, ge=1, le=30),
    bucket: AnalyticsBucket = Query("1d"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> AnalyticsOut:
    return _bound_task_keys(_authorized_analytics(db, user, park_id, days, bucket))


@router.get("/task-keys", response_model=AnalyticsTaskKeysPage)
def historical_task_keys(
    park_id: int = Query(..., ge=1),
    days: int = Query(7, ge=1, le=30),
    bucket: AnalyticsBucket = Query("1d"),
    period_end: datetime = Query(...),
    group: TaskKeysGroup = Query(...),
    key: str | None = Query(None),
    offset: int = Query(0, ge=0),
    after: str | None = Query(None, max_length=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> AnalyticsTaskKeysPage:
    now = datetime.now(UTC)
    if period_end.tzinfo is None or period_end > now or period_end < now - timedelta(days=31):
        raise HTTPException(400, "analytics_task_keys_period_invalid")
    report = _authorized_analytics(db, user, park_id, days, bucket, now=period_end)
    if report.period.end != period_end:
        raise HTTPException(400, "analytics_task_keys_period_invalid")
    keys = _task_keys(report, group, key)
    start = bisect_right(keys, after) if after is not None else offset
    return AnalyticsTaskKeysPage(
        task_keys=keys[start : start + TASK_KEYS_PAGE_SIZE],
        total=len(keys),
        has_more=start + TASK_KEYS_PAGE_SIZE < len(keys),
    )
