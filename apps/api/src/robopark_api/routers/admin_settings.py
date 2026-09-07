from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from robopark_api.crypto import MissingSecretKeyError
from robopark_api.db import get_db
from robopark_api.deps import require_admin, require_royal
from robopark_api.models import User
from robopark_api.schemas import (
    EmergencyCookieCheck,
    EmergencyCookieUpdate,
    IntegrationSettingsOut,
    ScreenshotGuardSettingsIn,
    ScreenshotGuardSettingsOut,
    TrackerPolicySettingsIn,
    TrackerPolicySettingsOut,
)
from robopark_api.services import audit, emergency_cache, emergency_client, emergency_vin
from robopark_api.services import platform_settings as settings_svc

router = APIRouter(
    prefix="/admin/settings",
    tags=["admin-settings"],
    dependencies=[Depends(require_admin)],
)


def _require_secret_key(exc: MissingSecretKeyError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="secret_key_required",
    )


class TrackerTokenUpdate(BaseModel):
    token: str = Field(min_length=1)


class RegistrationPasswordSettingsOut(BaseModel):
    configured: bool
    password_masked: str | None
    updated_at: str | None
    encrypted: bool = False


class RegistrationPasswordUpdate(BaseModel):
    password: str = Field(min_length=1, max_length=128)


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
        emergency_cookie_status=data["emergency_cookie_status"],
        emergency_cookie_checked_at=data["emergency_cookie_checked_at"],
        emergency_cookie_checked_robot=data["emergency_cookie_checked_robot"],
    )


@router.get("/integrations", response_model=IntegrationSettingsOut)
def get_integrations(db: Session = Depends(get_db)) -> IntegrationSettingsOut:
    return _to_out(db)


@router.put("/tracker-token", response_model=IntegrationSettingsOut)
def put_tracker_token(
    payload: TrackerTokenUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> IntegrationSettingsOut:
    from robopark_api.services import tracker_cache, tracker_client, tracker_metrics

    try:
        settings_svc.set_setting(db, settings_svc.TRACKER_TOKEN_KEY, payload.token)
    except MissingSecretKeyError as exc:
        raise _require_secret_key(exc) from exc
    tracker_client.clear_tracker_clients()
    tracker_metrics.clear_metrics_cache()
    tracker_cache.clear_all()
    audit.record(
        db,
        action=audit.ACTION_TRACKER_TOKEN_SET,
        actor=admin,
        detail="tracker token updated",
    )
    return _to_out(db)


@router.put("/emergency-cookie", response_model=IntegrationSettingsOut)
def put_emergency_cookie(
    payload: EmergencyCookieUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> IntegrationSettingsOut:
    vin = _normalize_probe_robot(payload.robot_number)
    checked_robot = emergency_vin.short_robot_number(vin)
    try:
        emergency_client.fetch_robot_payload(cookie=payload.cookie, vin=vin)
    except emergency_client.EmergencyAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="emergency_cookie_invalid",
        ) from exc
    except emergency_client.EmergencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="emergency_upstream_unavailable",
        ) from exc

    try:
        identity = settings_svc.activate_emergency_cookie(
            db,
            cookie=payload.cookie,
            status="valid",
            checked_robot=checked_robot,
        )
    except MissingSecretKeyError as exc:
        raise _require_secret_key(exc) from exc
    from robopark_api.services import reports as reports_svc

    emergency_cache.clear_cache()
    reports_svc.resolve_open_emergency_cookie_reports(db, expected_identity=identity)
    audit.record(
        db,
        action=audit.ACTION_EMERGENCY_COOKIE_SET,
        actor=admin,
        detail="emergency cookie updated",
    )
    return _to_out(db)


def _normalize_probe_robot(robot_number: str) -> str:
    try:
        return emergency_vin.normalize_robot_id(robot_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid_robot_number",
        ) from exc


def _last_keepalive_robot(db: Session) -> str | None:
    ring = settings_svc.get_keepalive_ring(db)
    return ring[-1] if ring else None


@router.post(
    "/emergency-cookie/check",
    response_model=IntegrationSettingsOut,
    responses={422: {"description": "A robot number or keepalive probe is required."}},
)
def check_emergency_cookie(
    payload: EmergencyCookieCheck = EmergencyCookieCheck(),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> IntegrationSettingsOut:
    probe_robot = payload.robot_number or _last_keepalive_robot(db)
    if probe_robot is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="emergency_probe_required",
        )

    vin = _normalize_probe_robot(probe_robot)
    checked_robot = emergency_vin.short_robot_number(vin)
    cookie, identity = settings_svc.get_emergency_cookie_probe(db)
    if not cookie:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="emergency_cookie_not_configured",
        )

    try:
        emergency_client.fetch_robot_payload(cookie=cookie, vin=vin)
    except emergency_client.EmergencyAuthError as exc:
        from robopark_api.services import reports as reports_svc

        if settings_svc.record_emergency_cookie_probe(
            db,
            identity=identity,
            valid=False,
            status="invalid",
            checked_robot=checked_robot,
        ):
            reports_svc.ensure_open_emergency_cookie_report(
                db,
                author=admin,
                expected_identity=identity,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="emergency_cookie_invalid",
        ) from exc
    except emergency_client.EmergencyError as exc:
        settings_svc.record_emergency_cookie_probe(
            db,
            identity=identity,
            valid=None,
            status="unavailable",
            checked_robot=checked_robot,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="emergency_upstream_unavailable",
        ) from exc

    from robopark_api.services import reports as reports_svc

    if settings_svc.record_emergency_cookie_probe(
        db,
        identity=identity,
        valid=True,
        status="valid",
        checked_robot=checked_robot,
    ):
        reports_svc.resolve_open_emergency_cookie_reports(db, expected_identity=identity)
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


@router.get("/screenshot-guard", response_model=ScreenshotGuardSettingsOut)
def get_screenshot_guard(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> ScreenshotGuardSettingsOut:
    return ScreenshotGuardSettingsOut(**settings_svc.screenshot_guard_status(db))


@router.put("/screenshot-guard", response_model=ScreenshotGuardSettingsOut)
def put_screenshot_guard(
    payload: ScreenshotGuardSettingsIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> ScreenshotGuardSettingsOut:
    if payload.operator is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.SCREENSHOT_GUARD_OPERATOR_KEY,
            payload.operator,
        )
    if payload.mechanic is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.SCREENSHOT_GUARD_MECHANIC_KEY,
            payload.mechanic,
        )
    if payload.admin is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.SCREENSHOT_GUARD_ADMIN_KEY,
            payload.admin,
        )
    if payload.royal is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.SCREENSHOT_GUARD_ROYAL_KEY,
            payload.royal,
        )
    if payload.driver is not None:
        settings_svc.set_bool_setting(
            db,
            settings_svc.SCREENSHOT_GUARD_DRIVER_KEY,
            payload.driver,
        )
    return ScreenshotGuardSettingsOut(**settings_svc.screenshot_guard_status(db))


@router.get("/registration-password", response_model=RegistrationPasswordSettingsOut)
def get_registration_password(
    db: Session = Depends(get_db),
    _royal: User = Depends(require_royal),
) -> RegistrationPasswordSettingsOut:
    return RegistrationPasswordSettingsOut(**settings_svc.registration_password_status(db))


@router.put("/registration-password", response_model=RegistrationPasswordSettingsOut)
def put_registration_password(
    payload: RegistrationPasswordUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
) -> RegistrationPasswordSettingsOut:
    try:
        settings_svc.set_registration_shared_password(db, payload.password)
    except MissingSecretKeyError as exc:
        raise _require_secret_key(exc) from exc
    audit.record(
        db,
        action=audit.ACTION_SETTINGS_CHANGED,
        actor=actor,
        detail="registration shared password updated",
    )
    return RegistrationPasswordSettingsOut(**settings_svc.registration_password_status(db))


@router.delete("/registration-password", response_model=RegistrationPasswordSettingsOut)
def delete_registration_password(
    db: Session = Depends(get_db),
    actor: User = Depends(require_royal),
) -> RegistrationPasswordSettingsOut:
    settings_svc.clear_registration_shared_password(db)
    audit.record(
        db,
        action=audit.ACTION_SETTINGS_CHANGED,
        actor=actor,
        detail="registration shared password cleared",
    )
    return RegistrationPasswordSettingsOut(**settings_svc.registration_password_status(db))
