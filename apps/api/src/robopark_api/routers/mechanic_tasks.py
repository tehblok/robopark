from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import get_mechanic_park, require_approved_mechanic
from robopark_api.models import User
from robopark_api.routers._blockers import blocker_out as _blocker_out
from robopark_api.schemas import MechanicTasksOut
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_cache, tracker_client, tracker_filters

router = APIRouter(prefix="/mechanic", tags=["mechanic-tasks"])


@router.get("/tasks", response_model=MechanicTasksOut)
def mechanic_tasks(
    status_filter: str = Query(default="all", alias="status"),
    user: User = Depends(require_approved_mechanic),
    db: Session = Depends(get_db),
) -> MechanicTasksOut:
    if status_filter not in tracker_filters.VALID_STATUS_FILTERS:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST)

    park = get_mechanic_park(db, user)
    if park is None:
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN)

    if not park.feature_blockers:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="tasks_disabled_for_park",
        )
    if not park.tracker_queue:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="tasks_disabled_for_park",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    try:
        priority, issue_type = tracker_filters.park_priority_type(park)
        issues = tracker_cache.fetch_park_blockers(
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
    return MechanicTasksOut(
        park_tag=park.tag,
        status=status_filter,
        counts=counts,
        items=[_blocker_out(item) for item in filtered[:200]],
    )
