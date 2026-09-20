from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.services import offline_sync, rbac
from robopark_api.sync_schemas import SyncBatchIn, SyncBatchOut

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/batch", response_model=SyncBatchOut)
def sync_batch(
    body: SyncBatchIn,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> SyncBatchOut:
    rbac.assert_approved_or_staff(user)
    return offline_sync.synchronize(
        db,
        user,
        body,
        revision_store=request.app.state.change_revision_store,
    )
