from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.operations_schemas import OperationsOverviewOut
from robopark_api.services import operations

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
