from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_royal
from robopark_api.models import PrivilegedCredential, User
from robopark_api.ops_schemas import HostOperationKind
from robopark_api.security import hash_session_token
from robopark_api.services import privileged_auth
from robopark_api.services.login_throttle import LoginThrottle, client_ip
from robopark_api.services.ops import host_bridge

router = APIRouter(prefix="/admin/privileged-auth", tags=["privileged-auth"])


class EnrollmentOut(BaseModel):
    secret: str


class ConfirmIn(BaseModel):
    password: str
    code: str


class RecoveryOut(BaseModel):
    recovery_codes: list[str]


class RecoveryResetOut(RecoveryOut):
    pending_id: str
    secret: str
    expires_in: int


class RecoveryResetConfirmIn(BaseModel):
    pending_id: str = Field(min_length=1, max_length=64)
    code: str


class ReauthorizeIn(ConfirmIn):
    operation_kind: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(min_length=1, max_length=128)
    capability_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def require_typed_capability_revision(self):
        typed = self.operation_kind in set(HostOperationKind)
        if typed and self.capability_revision is None:
            raise ValueError("capability_revision is required for typed operations")
        return self


class TokenOut(BaseModel):
    token: str
    expires_in: int


def _session_hash(request: Request, settings: Settings) -> str:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise HTTPException(status_code=401)
    return hash_session_token(raw)


def _device(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _throttle(settings: Settings) -> LoginThrottle:
    return LoginThrottle(
        max_attempts=settings.login_max_attempts,
        window_seconds=settings.login_attempt_window_seconds,
        lockout_seconds=settings.login_lockout_seconds,
    )


def _context(
    request: Request,
    settings: Settings | None = None,
    *,
    operation_kind: str | None = None,
    operation_id: str | None = None,
    capability_revision: str | None = None,
) -> privileged_auth.AuditContext:
    return privileged_auth.AuditContext(
        ip=client_ip(request),
        device=_device(request),
        session_token_hash=_session_hash(request, settings) if settings else None,
        operation_kind=operation_kind,
        operation_id=operation_id,
        capability_revision=capability_revision,
    )


def _raise(error: privileged_auth.PrivilegedAuthError) -> None:
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    raise HTTPException(
        status_code=error.status_code,
        detail=error.reason,
        headers=headers,
    ) from error


def _capability_error_status(detail: str) -> int:
    if detail == "invalid_command":
        return 422
    if detail in {"capability_unavailable", "capabilities_changed"}:
        return 409
    return 503


@router.get("/status")
def enrollment_status(
    royal: User = Depends(require_royal), db: Session = Depends(get_db)
) -> dict[str, bool]:
    row = db.get(PrivilegedCredential, royal.id)
    return {"enrolled": bool(row and row.enrolled_at)}


@router.post("/enrollment", response_model=EnrollmentOut)
def begin_enrollment(
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EnrollmentOut:
    try:
        secret = privileged_auth.begin_enrollment(db, royal, settings, context=_context(request))
    except privileged_auth.PrivilegedAuthError as exc:
        _raise(exc)
    return EnrollmentOut(secret=secret)


@router.post("/enrollment/confirm", response_model=RecoveryOut)
def confirm_enrollment(
    payload: ConfirmIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RecoveryOut:
    ip = client_ip(request)
    throttle = _throttle(settings)
    throttle_key = f"privileged-enrollment|{royal.id}|{ip}"
    try:
        codes = privileged_auth.confirm_enrollment(
            db,
            royal,
            settings,
            password=payload.password,
            code=payload.code,
            context=_context(request),
            throttle=throttle,
            throttle_key=throttle_key,
        )
    except privileged_auth.PrivilegedAuthError as exc:
        _raise(exc)
    return RecoveryOut(recovery_codes=codes)


@router.post("/reauthorize", response_model=TokenOut)
def reauthorize(
    payload: ReauthorizeIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenOut:
    try:
        host_bridge.require_typed_reauthorization(
            settings,
            payload.operation_kind,
            payload.operation_id,
            payload.capability_revision,
        )
    except host_bridge.BridgeError as exc:
        detail = str(exc)
        raise HTTPException(
            status_code=_capability_error_status(detail),
            detail=detail,
        ) from exc
    ip = client_ip(request)
    throttle = _throttle(settings)
    throttle_key = f"privileged|{royal.id}|{ip}"

    def validate_capability_context() -> tuple[str, int] | None:
        try:
            host_bridge.require_typed_reauthorization(
                settings,
                payload.operation_kind,
                payload.operation_id,
                payload.capability_revision,
            )
        except host_bridge.BridgeError as exc:
            detail = str(exc)
            return detail, _capability_error_status(detail)
        return None

    try:
        token = privileged_auth.issue_reauthorization(
            db,
            royal,
            settings,
            password=payload.password,
            code=payload.code,
            context=_context(
                request,
                settings,
                operation_kind=payload.operation_kind,
                operation_id=payload.operation_id,
                capability_revision=payload.capability_revision,
            ),
            throttle=throttle,
            throttle_key=throttle_key,
            validate_context=validate_capability_context,
        )
    except privileged_auth.PrivilegedAuthError as exc:
        _raise(exc)
    return TokenOut(token=token, expires_in=privileged_auth.REAUTH_TTL_SECONDS)


@router.post("/recovery/reset", response_model=RecoveryResetOut)
def reset_recovery(
    payload: ConfirmIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RecoveryResetOut:
    throttle = _throttle(settings)
    throttle_key = f"privileged-recovery|{royal.id}|{client_ip(request)}"
    try:
        pending_id, secret, codes = privileged_auth.begin_recovery_reset(
            db,
            royal,
            settings,
            password=payload.password,
            code=payload.code,
            context=_context(request),
            throttle=throttle,
            throttle_key=throttle_key,
        )
    except privileged_auth.PrivilegedAuthError as exc:
        _raise(exc)
    return RecoveryResetOut(
        pending_id=pending_id,
        secret=secret,
        recovery_codes=codes,
        expires_in=privileged_auth.RECOVERY_RESET_TTL_SECONDS,
    )


@router.post("/recovery/reset/confirm", status_code=204)
def confirm_recovery_reset(
    payload: RecoveryResetConfirmIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    throttle = _throttle(settings)
    throttle_key = f"privileged-recovery|{royal.id}|{client_ip(request)}"
    try:
        privileged_auth.confirm_recovery_reset(
            db,
            royal,
            settings,
            pending_id=payload.pending_id,
            code=payload.code,
            context=_context(request),
            throttle=throttle,
            throttle_key=throttle_key,
        )
    except privileged_auth.PrivilegedAuthError as exc:
        _raise(exc)


@router.post("/recovery/rotate", response_model=RecoveryOut)
def rotate_recovery(
    payload: ConfirmIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RecoveryOut:
    throttle = _throttle(settings)
    throttle_key = f"privileged-recovery|{royal.id}|{client_ip(request)}"
    try:
        codes = privileged_auth.rotate_recovery_codes(
            db,
            royal,
            settings,
            password=payload.password,
            code=payload.code,
            context=_context(request),
            throttle=throttle,
            throttle_key=throttle_key,
        )
    except privileged_auth.PrivilegedAuthError as exc:
        _raise(exc)
    return RecoveryOut(recovery_codes=codes)
