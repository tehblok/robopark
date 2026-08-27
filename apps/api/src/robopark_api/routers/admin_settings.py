from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_admin
from robopark_api.models import User
from robopark_api.schemas import TrackerPolicySettingsIn, TrackerPolicySettingsOut
from robopark_api.services import platform_settings as settings_svc

router = APIRouter(
    prefix="/admin/settings",
    tags=["admin-settings"],
    dependencies=[Depends(require_admin)],
)


class IntegrationSettingsOut(BaseModel):
    tracker_token_masked: str | None
    tracker_token_updated_at: str | None
    tracker_token_encrypted: bool = False
    emergency_cookie_masked: str | None
    emergency_cookie_updated_at: str | None
    emergency_cookie_encrypted: bool = False
    emergency_cookie_valid: bool | None


class TrackerTokenUpdate(BaseModel):
    token: str = Field(min_length=1)


class EmergencyCookieUpdate(BaseModel):
    cookie: str = Field(min_length=1)


def _to_out(db: Session) -> IntegrationSettingsOut:
    data = settings_svc.integration_status(db)
    return IntegrationSettingsOut(
        tracker_token_masked=data["tracker_token_masked"],
        tracker_token_updated_at=(
            data["tracker_token_updated_at"].isoformat()
            if data["tracker_token_updated_at"]
            else None
        ),
        tracker_token_encrypted=bool(data.get("tracker_token_encrypted")),
        emergency_cookie_masked=data["emergency_cookie_masked"],
        emergency_cookie_updated_at=(
            data["emergency_cookie_updated_at"].isoformat()
            if data["emergency_cookie_updated_at"]
            else None
        ),
        emergency_cookie_encrypted=bool(data.get("emergency_cookie_encrypted")),
        emergency_cookie_valid=data["emergency_cookie_valid"],
    )


@router.get("/integrations", response_model=IntegrationSettingsOut)
def get_integrations(db: Session = Depends(get_db)) -> IntegrationSettingsOut:
    return _to_out(db)


@router.put("/tracker-token", response_model=IntegrationSettingsOut)
def put_tracker_token(
    payload: TrackerTokenUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> IntegrationSettingsOut:
    from robopark_api.services import tracker_cache, tracker_client, tracker_metrics

    settings_svc.set_setting(db, settings_svc.TRACKER_TOKEN_KEY, payload.token)
    tracker_client.clear_tracker_clients()
    tracker_metrics.clear_metrics_cache()
    tracker_cache.clear_all()
    return _to_out(db)


@router.put("/emergency-cookie", response_model=IntegrationSettingsOut)
def put_emergency_cookie(
    payload: EmergencyCookieUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> IntegrationSettingsOut:
    settings_svc.set_setting(db, settings_svc.EMERGENCY_COOKIE_KEY, payload.cookie)
    settings_svc.set_emergency_cookie_valid(db, True)
    from robopark_api.services import reports as reports_svc

    reports_svc.resolve_open_emergency_cookie_reports(db)
    return _to_out(db)


@router.get("/tracker-policy", response_model=TrackerPolicySettingsOut)
def get_tracker_policy(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> TrackerPolicySettingsOut:
    return TrackerPolicySettingsOut(**settings_svc.tracker_policy_status(db))


@router.put("/tracker-policy", response_model=TrackerPolicySettingsOut)
def put_tracker_policy(
    payload: TrackerPolicySettingsIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> TrackerPolicySettingsOut:
    if payload.operator_show_untagged is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.TRACKER_OPERATOR_UNTAGGED_KEY,
            payload.operator_show_untagged,
        )
    if payload.operator_show_raw is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.TRACKER_OPERATOR_RAW_KEY,
            payload.operator_show_raw,
        )
    if payload.operator_show_firmware_profile is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.TRACKER_OPERATOR_FIRMWARE_KEY,
            payload.operator_show_firmware_profile,
        )
    if payload.mechanic_can_write is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.TRACKER_MECHANIC_WRITE_KEY,
            payload.mechanic_can_write,
        )
    return TrackerPolicySettingsOut(**settings_svc.tracker_policy_status(db))
