from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import (
    get_operator_parks,
    require_approved_operator,
    require_operator_park,
)
from robopark_api.models import User
from robopark_api.schemas import NowReportOut, ParkMetricsOut, SkippedParkOut
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_client, tracker_metrics

router = APIRouter(prefix="/operator", tags=["operator-report"])

EMPTY_TOTALS = {
    "blocker": 0,
    "backlog": 0,
    "in_transit": 0,
    "queued": 0,
    "waiting_team": 0,
    "waiting_parts": 0,
    "arrived": 0,
    "done": 0,
}

TOTAL_FROM_PARK = {
    "blocker": "open_blockers",
    "backlog": "backlog",
    "in_transit": "in_transit",
    "queued": "queued",
    "waiting_team": "waiting_team",
    "waiting_parts": "waiting_parts",
    "arrived": "arrived",
    "done": "done",
}


@router.get("/now-report", response_model=NowReportOut)
def operator_now_report(
    park_id: int | None = Query(default=None),
    user: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> NowReportOut:
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    if park_id is not None:
        parks = [require_operator_park(park_id, db, user)]
        scope = "park"
        cache_key = f"now:{user.id}:{park_id}"
    else:
        parks = get_operator_parks(db, user)
        scope = "all"
        cache_key = f"now:{user.id}:all"

    if not parks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no_report_parks",
        )

    cached = tracker_metrics.get_cached_now_report(cache_key)
    if cached is not None:
        return NowReportOut(**cached)

    totals = dict(EMPTY_TOTALS)
    park_rows: list[ParkMetricsOut] = []
    skipped: list[SkippedParkOut] = []
    counted_keys: set[tuple[str, str]] = set()

    for park in parks:
        if not park.feature_reports:
            skipped.append(
                SkippedParkOut(
                    park_id=park.id, park_name=park.name, reason="reports_disabled"
                )
            )
            continue
        queue = (park.tracker_queue or "").strip()
        if not queue:
            skipped.append(
                SkippedParkOut(
                    park_id=park.id, park_name=park.name, reason="no_tracker_queue"
                )
            )
            continue
        try:
            metrics = tracker_metrics.collect_park_metrics(
                token=token, queue=queue, tag=park.tag
            )
        except tracker_client.TrackerError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc

        park_rows.append(
            ParkMetricsOut(
                park_id=park.id,
                park_name=park.name,
                park_tag=park.tag,
                metrics=metrics,
            )
        )
        dedupe = (queue, park.tag)
        if dedupe in counted_keys:
            continue
        counted_keys.add(dedupe)
        for total_key, metric_key in TOTAL_FROM_PARK.items():
            totals[total_key] += int(metrics.get(metric_key) or 0)

    payload = {
        "generated_at": datetime.now(ZoneInfo("Europe/Moscow")).isoformat(),
        "scope": scope,
        "totals": totals,
        "parks": [p.model_dump() for p in park_rows],
        "skipped_parks": [s.model_dump() for s in skipped],
    }
    tracker_metrics.set_cached_now_report(cache_key, payload)
    return NowReportOut(**payload)
