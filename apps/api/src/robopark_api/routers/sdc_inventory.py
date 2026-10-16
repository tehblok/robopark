"""Scoped SDC Inventory facts for robot checks, including the AI assistant."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_emergency_viewer
from robopark_api.models import User
from robopark_api.routers.emergency import authorize_emergency_vin
from robopark_api.services import (
    corporate_gateway_client,
    inventory_server_circuit,
    platform_settings,
    sdc_inventory,
)

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
        raise _inventory_http_error(exc) from None


def _inventory_http_error(exc: sdc_inventory.InventoryError) -> HTTPException:
    code = str(exc)
    status = {
        "sdc_inventory_access_denied": 403,
        "sdc_inventory_not_found": 404,
        "sdc_inventory_rate_limited": 429,
        "sdc_inventory_busy": 503,
    }.get(code, 502)
    return HTTPException(status, code)


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


def _service_token(db):
    token = platform_settings.get_tracker_token(db)
    if not token:
        raise HTTPException(503, "sdc_inventory_token_not_configured")
    return token


def _server_read(function, db, *args, endpoint, **kwargs):
    """Return (result, availability_error); never hide ACL/not-found failures."""
    try:
        token = _service_token(db)
    except HTTPException as exc:
        return None, exc
    circuit_key = inventory_server_circuit.inventory_server_circuit.key(
        endpoint=endpoint,
        base_url=sdc_inventory.BASE_URL,
        credential=token,
    )
    admission = inventory_server_circuit.inventory_server_circuit.allow(circuit_key)
    if admission is None:
        return None, HTTPException(503, "sdc_inventory_unavailable")
    try:
        result = function(token, *args, **kwargs)
    except sdc_inventory.InventoryError as exc:
        if isinstance(exc, sdc_inventory.InventoryAvailabilityError):
            inventory_server_circuit.inventory_server_circuit.unavailable(circuit_key, admission)
            return None, HTTPException(503, str(exc))
        inventory_server_circuit.inventory_server_circuit.available(circuit_key, admission)
        raise _inventory_http_error(exc) from None
    except BaseException:
        inventory_server_circuit.inventory_server_circuit.available(circuit_key, admission)
        raise
    inventory_server_circuit.inventory_server_circuit.available(circuit_key, admission)
    return result, None


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
    result, server_error = _server_read(sdc_inventory.fetch_card, db, name, endpoint="rover_card")
    if result is not None:
        return result
    if corporate_gateway_client.enabled(settings):
        payload = _gateway_read(
            action="rover_card",
            name=name,
            params={},
            user=user,
            park_id=park_id,
            settings=settings,
        )
        return _read(sdc_inventory.project_card, payload, name)
    assert server_error is not None
    raise server_error


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
    params = {"offset": offset, "limit": limit}
    if slot is not None:
        params["slot"] = slot
    result, server_error = _server_read(
        sdc_inventory.fetch_installations,
        db,
        name,
        endpoint="rover_installations",
        offset=offset,
        limit=limit,
        slot=slot,
    )
    if result is not None:
        return result
    if corporate_gateway_client.enabled(settings):
        payload = _gateway_read(
            action="rover_installations",
            name=name,
            params=params,
            user=user,
            park_id=park_id,
            settings=settings,
        )
        return _read(
            sdc_inventory.project_installations,
            payload,
            name,
            offset=offset,
            limit=limit,
            slot=slot,
        )
    assert server_error is not None
    raise server_error
