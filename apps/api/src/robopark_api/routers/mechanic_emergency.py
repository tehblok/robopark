from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_approved_mechanic
from robopark_api.models import User
from robopark_api.routers.emergency import (
    emergency_section_for_user,
    resolve_robot_for_user,
)
from robopark_api.schemas import (
    EmergencyResolveOut,
    EmergencyResolveRequest,
    EmergencySectionOut,
)

router = APIRouter(prefix="/mechanic/emergency", tags=["mechanic-emergency"])


@router.post("/resolve", response_model=EmergencyResolveOut)
def resolve_robot(
    payload: EmergencyResolveRequest,
    user: User = Depends(require_approved_mechanic),
    db: Session = Depends(get_db),
) -> EmergencyResolveOut:
    return resolve_robot_for_user(payload, user, db)


@router.get("/{vin}/sections/{section_id}", response_model=EmergencySectionOut)
def emergency_section(
    vin: str,
    section_id: str,
    user: User = Depends(require_approved_mechanic),
    db: Session = Depends(get_db),
) -> EmergencySectionOut:
    return emergency_section_for_user(vin, section_id, user, db)
