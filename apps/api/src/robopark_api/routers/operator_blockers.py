from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_approved_operator, require_operator_park
from robopark_api.models import User
from robopark_api.routers._blockers import blocker_out as _blocker_out
from robopark_api.schemas import OperatorBlockersOut
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_client, tracker_filters

router = APIRouter(prefix="/operator", tags=["operator-blockers"])


@router.get("/blockers", response_model=OperatorBlockersOut)
def operator_blockers(
    park_id: int = Query(...),
    status_filter: str = Query(default="all", alias="status"),
    user: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> OperatorBlockersOut:
    if status_filter not in tracker_filters.VALID_STATUS_FILTERS:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST)

    park = require_operator_park(park_id, db, user)

    if not park.feature_blockers or not park.tracker_queue:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="blockers_disabled_for_park",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    try:
        priority, issue_type = tracker_filters.park_priority_type(park)
        issues = tracker_client.fetch_park_blockers(
            token=token,
            queue=park.tracker_queue,
            park_tag=park.tag,
            priority=priority,
            issue_type=issue_type,
        )
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    sorted_issues = tracker_filters.sort_issues_oldest_first(issues)
    filtered = tracker_filters.filter_issues_by_status(sorted_issues, status_filter)
    counts = tracker_filters.count_status_buckets(sorted_issues)
    return OperatorBlockersOut(
        park_id=park.id,
        park_tag=park.tag,
        status=status_filter,
        counts=counts,
        items=[_blocker_out(item) for item in filtered[:200]],
    )
