from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_emergency_viewer
from robopark_api.models import User
from robopark_api.schemas import (
    EmergencyFieldOut,
    EmergencyResolveOut,
    EmergencyResolveRequest,
    EmergencySectionItem,
    EmergencySectionOut,
)
from robopark_api.services import (
    emergency_cache,
    emergency_client,
    emergency_sections,
    emergency_vin,
)
from robopark_api.services import platform_settings as settings_svc

router = APIRouter(prefix="/emergency", tags=["emergency"])


def _require_emergency_cookie(db: Session) -> None:
    if not settings_svc.get_emergency_cookie(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="emergency_cookie_not_configured",
        )


def _get_robot_payload(db: Session, vin: str) -> dict:
    _require_emergency_cookie(db)
    try:
        return emergency_cache.get_robot_payload(db=db, vin=vin)
    except emergency_client.EmergencyAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="emergency_cookie_invalid",
        ) from exc
    except emergency_client.EmergencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc


def resolve_robot_for_user(
    payload: EmergencyResolveRequest, user: User, db: Session
) -> EmergencyResolveOut:
    try:
        vin = emergency_vin.normalize_robot_id(payload.robot_number)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    _get_robot_payload(db, vin)
    sections = [
        EmergencySectionItem(id=section_id, title=title)
        for section_id, title in emergency_sections.list_sections(db, user.role)
    ]
    return EmergencyResolveOut(vin=vin, sections=sections)


def emergency_section_for_user(
    vin: str, section_id: str, user: User, db: Session
) -> EmergencySectionOut:
    try:
        vin = emergency_vin.normalize_robot_id(vin)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    payload = _get_robot_payload(db, vin)
    try:
        rendered = emergency_sections.render_section(
            db, payload, section_id, role=user.role
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from exc

    return EmergencySectionOut(
        id=rendered["id"],
        title=rendered["title"],
        fields=[
            EmergencyFieldOut(label=field["label"], lines=field["lines"])
            for field in rendered["fields"]
        ],
    )


@router.post("/resolve", response_model=EmergencyResolveOut)
def resolve_robot(
    payload: EmergencyResolveRequest,
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
) -> EmergencyResolveOut:
    return resolve_robot_for_user(payload, user, db)


@router.get("/{vin}/sections/{section_id}", response_model=EmergencySectionOut)
def emergency_section(
    vin: str,
    section_id: str,
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
) -> EmergencySectionOut:
    return emergency_section_for_user(vin, section_id, user, db)
