"""Scoped SDC Inventory facts for robot checks, including the AI assistant."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_emergency_viewer
from robopark_api.models import User
from robopark_api.routers.emergency import authorize_emergency_vin
from robopark_api.services import corporate_gateway_client, platform_settings, sdc_inventory

router = APIRouter(prefix="/sdc-inventory", tags=["sdc-inventory"])


def _access(db, user, robot, park_id):
    try:
        name = sdc_inventory.robot_name(robot)
    except ValueError:
        raise HTTPException(422, "invalid_robot_number") from None
    authorize_emergency_vin(db, user, name, park_id=park_id)
    return name


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


def _gateway_read(*, action, name, params, user, park_id, settings):
    try:
        return corporate_gateway_client.read(
            service="inventory",
            action=action,
            resource=name,
            params=params,
            requester_user_id=user.id,
            park_id=park_id,
            settings=settings,
        ).payload
    except corporate_gateway_client.CorporateGatewayError as exc:
        raise HTTPException(exc.status_code, exc.code) from None


def _gateway_or_none(*, action, name, params, user, park_id, settings):
    try:
        return _gateway_read(
            action=action,
            name=name,
            params=params,
            user=user,
            park_id=park_id,
            settings=settings,
        )
    except HTTPException as exc:
        if (
            exc.status_code == 503
            and exc.detail in {"corporate_unavailable", "corporate_gateway_disabled"}
            and settings.mac_corporate_server_fallback_enabled
        ):
            return None
        raise


def _service_token(db):
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise HTTPException(503, "sdc_inventory_token_not_configured")
    return token


@router.get("/robots/{robot}", summary="SDC Inventory: карточка, локация и состояние робота")
def robot_card(
    robot: str = Path(min_length=1, max_length=64),
    park_id: int | None = Query(default=None, gt=0),
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Учётные факты; fleet — тип флота, port/storage — локация. Не физический осмотр."""
    name = _access(db, user, robot, park_id)
    if corporate_gateway_client.enabled(settings):
        payload = _gateway_or_none(
            action="rover_card",
            name=name,
            params={},
            user=user,
            park_id=park_id,
            settings=settings,
        )
        if payload is not None:
            return _read(sdc_inventory.project_card, payload, name)
    return _read(sdc_inventory.fetch_card, _service_token(db), name)


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
    settings: Settings = Depends(get_settings),
):
    """Постраничные установки. Сравни серийные номера с осмотром человека; учитывай status и complete."""
    name = _access(db, user, robot, park_id)
    if corporate_gateway_client.enabled(settings):
        params = {"offset": offset, "limit": limit}
        if slot is not None:
            params["slot"] = slot
        payload = _gateway_or_none(
            action="rover_installations",
            name=name,
            params=params,
            user=user,
            park_id=park_id,
            settings=settings,
        )
        if payload is not None:
            return _read(
                sdc_inventory.project_installations,
                payload,
                name,
                offset=offset,
                limit=limit,
                slot=slot,
            )
    return _read(
        sdc_inventory.fetch_installations,
        _service_token(db),
        name,
        offset=offset,
        limit=limit,
        slot=slot,
    )
