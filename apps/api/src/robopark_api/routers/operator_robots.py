from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import get_operator_parks, require_approved_operator
from robopark_api.models import User
from robopark_api.schemas import BlockerOut, RobotTicketsOut
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_client, tracker_filters

router = APIRouter(prefix="/operator", tags=["operator-robots"])


def _blocker_out(item: dict) -> BlockerOut:
    return BlockerOut(
        key=item["key"],
        summary=item["summary"],
        status=item["status"],
        robot=item.get("robot"),
        created_at=item.get("created"),
        hours_created=item.get("hours_created"),
        url=tracker_client.build_issue_url(item["key"]),
        bucket=tracker_filters.issue_status_bucket(item),
    )


@router.get("/robots/{query}/tickets", response_model=RobotTicketsOut)
def operator_robot_tickets(
    query: str,
    user: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> RobotTicketsOut:
    parks = get_operator_parks(db, user)
    queues: list[str] = []
    seen: set[str] = set()
    for park in parks:
        q = (park.tracker_queue or "").strip()
        if q and q not in seen:
            seen.add(q)
            queues.append(q)
    if not queues:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no_tracker_parks",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    allowed_queues = set(queues)
    merged: list[dict] = []
    keys: set[str] = set()
    try:
        for queue in queues:
            for item in tracker_client.search_robot_tickets(
                token=token, queue=queue, query=query
            ):
                item_queue = (item.get("queue") or "").strip()
                if item_queue and item_queue not in allowed_queues:
                    continue
                if item["key"] in keys:
                    continue
                keys.add(item["key"])
                merged.append(item)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    sorted_items = tracker_filters.sort_issues_oldest_first(merged)
    return RobotTicketsOut(
        query=query,
        items=[_blocker_out(item) for item in sorted_items],
    )
