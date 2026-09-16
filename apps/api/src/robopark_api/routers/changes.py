"""Authenticated version-only hints; protected data still comes from regular APIs."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import get_user_parks, require_user
from robopark_api.models import User
from robopark_api.services import inventory_access, rbac
from robopark_api.services.change_revisions import ChangeRevisionStore

router = APIRouter(prefix="/changes", tags=["changes"])


def get_change_store(request: Request) -> ChangeRevisionStore:
    return request.app.state.change_revision_store


@router.get("")
def change_revision(
    scope: str,
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    store: ChangeRevisionStore = Depends(get_change_store),
) -> dict[str, int]:
    rbac.assert_approved_or_staff(user)
    allowed = False
    if scope == "work":
        allowed = rbac.is_admin_or_royal(user) and rbac.has_permission(
            db, user, rbac.PERMISSION_TRACKER_READ
        )
    elif scope == "work:mine":
        allowed = rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ)
    elif scope == "inventory:catalog":
        allowed = rbac.has_permission(db, user, rbac.PERMISSION_NAV_INVENTORY)
    elif scope.startswith("inventory:"):
        try:
            park_id = int(scope.split(":", 1)[1])
        except ValueError:
            raise HTTPException(status_code=404) from None
        allowed = inventory_access.can_view_park(db, user, park_id)
    else:
        raise HTTPException(status_code=404)
    if not allowed:
        raise HTTPException(status_code=403)
    response.headers["Cache-Control"] = "private, no-store"
    if scope == "work:mine":
        revision = sum(store.current(f"work:park:{park.id}") for park in get_user_parks(db, user))
    else:
        revision = store.current(scope)
    if scope.startswith("inventory:") and scope != "inventory:catalog":
        revision += store.current("inventory:catalog")
    return {"revision": revision}
