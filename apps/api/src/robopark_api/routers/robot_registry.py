from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.services.robot_registry import RegistryState, registry_rows

router = APIRouter(prefix="/robots", tags=["robot-registry"])


@router.get("")
def robot_registry(
    park_id: int | None = Query(default=None, ge=1),
    query: str = Query(default="", max_length=128),
    state: RegistryState = "all",
    active_errors: bool = False,
    open_tasks: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    return registry_rows(
        db,
        user,
        park_id=park_id,
        query=query,
        state=state,
        active_errors=active_errors,
        open_tasks=open_tasks,
        offset=offset,
        limit=limit,
    )
