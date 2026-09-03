import json

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.operations_schemas import OperationsOverviewOut, SlaPolicyOut, SlaPolicyUpdate
from robopark_api.services import audit, operations, platform_settings, rbac

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/overview", response_model=OperationsOverviewOut)
def overview(
    park_id: int = Query(..., ge=1),
    days: int = Query(7, ge=1, le=30),
    selected_status: str = Query("all", alias="status", max_length=64),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> OperationsOverviewOut:
    operations.require_operations_read(db, user)
    park = operations.require_operations_park(db, user, park_id)
    return operations.build_overview(db, user, park, days=days, selected_status=selected_status)


@router.get("/sla-policy", response_model=SlaPolicyOut)
def sla_policy(
    park_id: int = Query(..., ge=1),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> SlaPolicyOut:
    operations.require_operations_read(db, user, policy_only=True)
    park = operations.require_operations_park(db, user, park_id)
    return SlaPolicyOut(park_id=park.id, target_hours=operations.get_sla_target(db, park.id))


@router.put("/sla-policy", response_model=SlaPolicyOut)
def update_sla_policy(
    payload: SlaPolicyUpdate,
    request: Request,
    park_id: int = Query(..., ge=1),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> SlaPolicyOut:
    rbac.require_approved_permission(db, user, rbac.PERMISSION_PARKS_MANAGE)
    park = operations.require_operations_park(db, user, park_id)
    previous = operations.get_sla_target(db, park.id)
    platform_settings.set_setting(
        db, operations.sla_policy_key(park.id), json.dumps(payload.target_hours)
    )
    audit.record(
        db,
        action="operations.sla_policy.updated",
        actor=user,
        park_id=park.id,
        target_type="park",
        target_id=str(park.id),
        detail=json.dumps(
            {"previous_target_hours": previous, "target_hours": payload.target_hours}
        ),
        client_ip=request.client.host if request.client else None,
    )
    return SlaPolicyOut(park_id=park.id, target_hours=payload.target_hours)
