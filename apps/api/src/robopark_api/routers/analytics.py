from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from robopark_api.analytics_schemas import AnalyticsBucket, AnalyticsOut
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.services import analytics, rbac
from robopark_api.services.operations import ROLE_STATUSES, require_operations_park

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("", response_model=AnalyticsOut)
def historical_analytics(
    park_id: int = Query(..., ge=1),
    days: int = Query(7, ge=1, le=30),
    bucket: AnalyticsBucket = Query("1d"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> AnalyticsOut:
    rbac.require_approved_permission(db, user, rbac.PERMISSION_NAV_ANALYTICS)
    rbac.require_approved_permission(db, user, rbac.PERMISSION_TRACKER_READ)
    park = require_operations_park(db, user, park_id)
    return analytics.build_analytics(
        db, park_id=park.id, days=days, bucket=bucket, allowed_statuses=ROLE_STATUSES.get(user.role)
    )
