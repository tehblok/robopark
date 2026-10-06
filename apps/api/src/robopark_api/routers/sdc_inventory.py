"""Scoped SDC Inventory facts for robot checks, including the AI assistant."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_emergency_viewer
from robopark_api.models import User
from robopark_api.routers.emergency import authorize_emergency_vin
from robopark_api.services import platform_settings, sdc_inventory

router = APIRouter(prefix="/sdc-inventory", tags=["sdc-inventory"])


def _access(db, user, robot, park_id):
    try:
        name = sdc_inventory.robot_name(robot)
    except ValueError:
        raise HTTPException(422, "invalid_robot_number") from None
    authorize_emergency_vin(db, user, name, park_id=park_id)
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise HTTPException(503, "sdc_inventory_token_not_configured")
    return name, token


def _read(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except sdc_inventory.InventoryError as exc:
        code = str(exc)
        status = {
            "sdc_inventory_access_denied": 403,
            "sdc_inventory_not_found": 404,
            "sdc_inventory_busy": 503,
        }.get(code, 502)
        raise HTTPException(status, code) from None


@router.get("/robots/{robot}", summary="SDC Inventory: карточка, локация и состояние робота")
def robot_card(
    robot: str = Path(min_length=1, max_length=64),
    park_id: int | None = Query(default=None, gt=0),
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
):
    """Учётные факты; fleet — тип флота, port/storage — локация. Не физический осмотр."""
    name, token = _access(db, user, robot, park_id)
    return _read(sdc_inventory.fetch_card, token, name)


@router.get(
    "/robots/{robot}/installations", summary="SDC Inventory: комплектация и маркировка узлов"
)
def robot_installations(
    robot: str = Path(min_length=1, max_length=64),
    park_id: int | None = Query(default=None, gt=0),
    offset: int = Query(default=0, ge=0, le=100000),
    limit: int = Query(default=25, ge=1, le=50),
    slot: str | None = Query(default=None, pattern=r"^/[a-zA-Z0-9_/-]{1,200}$"),
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
):
    """Постраничные установки. Сравни серийные номера с осмотром человека; учитывай status и complete."""
    name, token = _access(db, user, robot, park_id)
    return _read(
        sdc_inventory.fetch_installations, token, name, offset=offset, limit=limit, slot=slot
    )
