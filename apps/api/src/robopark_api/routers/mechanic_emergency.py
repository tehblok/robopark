from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_approved_mechanic
from robopark_api.models import User
from robopark_api.schemas import (
    EmergencyFieldOut,
    EmergencyResolveOut,
    EmergencyResolveRequest,
    EmergencySectionItem,
    EmergencySectionOut,
)
from robopark_api.services import emergency_client, emergency_sections, emergency_vin
from robopark_api.services import platform_settings as settings_svc

router = APIRouter(prefix="/mechanic/emergency", tags=["mechanic-emergency"])


@router.post("/resolve", response_model=EmergencyResolveOut)
def resolve_robot(
    payload: EmergencyResolveRequest,
    _user: User = Depends(require_approved_mechanic),
    db: Session = Depends(get_db),
) -> EmergencyResolveOut:
    cookie = settings_svc.get_emergency_cookie(db)
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="emergency_cookie_not_configured",
        )
    try:
        vin = emergency_vin.normalize_robot_id(payload.robot_number)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        emergency_client.fetch_robot_payload(cookie=cookie, vin=vin)
        settings_svc.set_emergency_cookie_valid(db, True)
    except emergency_client.EmergencyAuthError as exc:
        settings_svc.set_emergency_cookie_valid(db, False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="emergency_cookie_invalid",
        ) from exc
    except emergency_client.EmergencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    sections = [
        EmergencySectionItem(id=section_id, title=title)
        for section_id, title in emergency_sections.list_sections()
    ]
    return EmergencyResolveOut(vin=vin, sections=sections)


@router.get("/{vin}/sections/{section_id}", response_model=EmergencySectionOut)
def emergency_section(
    vin: str,
    section_id: str,
    _user: User = Depends(require_approved_mechanic),
    db: Session = Depends(get_db),
) -> EmergencySectionOut:
    cookie = settings_svc.get_emergency_cookie(db)
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="emergency_cookie_not_configured",
        )
    try:
        payload = emergency_client.fetch_robot_payload(cookie=cookie, vin=vin)
        settings_svc.set_emergency_cookie_valid(db, True)
    except emergency_client.EmergencyAuthError as exc:
        settings_svc.set_emergency_cookie_valid(db, False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="emergency_cookie_invalid",
        ) from exc
    except emergency_client.EmergencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    try:
        rendered = emergency_sections.render_section(payload, section_id)
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
