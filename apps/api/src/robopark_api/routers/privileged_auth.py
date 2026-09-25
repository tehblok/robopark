from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_royal
from robopark_api.models import PrivilegedCredential, User
from robopark_api.security import hash_session_token
from robopark_api.services import privileged_auth
from robopark_api.services.login_throttle import LoginThrottle, client_ip

router = APIRouter(prefix="/admin/privileged-auth", tags=["privileged-auth"])


class EnrollmentOut(BaseModel):
    secret: str


class ConfirmIn(BaseModel):
    password: str
    code: str


class RecoveryOut(BaseModel):
    recovery_codes: list[str]


class ReauthorizeIn(ConfirmIn):
    operation_kind: str = Field(min_length=1, max_length=64)
    operation_id: str = Field(min_length=1, max_length=128)


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
        secret = privileged_auth.begin_enrollment(db, royal, settings)
    except privileged_auth.PrivilegedAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    privileged_auth.audit_decision(
        db, actor=royal, action="privileged.enrollment.begin", outcome="success",
        reason="pending", ip=client_ip(request), device=_device(request)
    )
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
    retry = throttle.retry_after(throttle_key, db=db)
    if retry:
        privileged_auth.audit_decision(
            db, actor=royal, action="privileged.enrollment.confirm", outcome="denied",
            reason="locked", ip=ip, device=_device(request)
        )
        raise HTTPException(status_code=429, detail="locked", headers={"Retry-After": str(retry)})
    try:
        codes = privileged_auth.confirm_enrollment(
            db, royal, settings, password=payload.password, code=payload.code
        )
    except privileged_auth.PrivilegedAuthError as exc:
        throttle.register_failure(throttle_key, db=db)
        privileged_auth.audit_decision(
            db, actor=royal, action="privileged.enrollment.confirm", outcome="denied",
            reason=str(exc), ip=client_ip(request), device=_device(request)
        )
        raise HTTPException(status_code=401, detail="invalid_credentials") from exc
    throttle.reset(throttle_key, db=db)
    privileged_auth.audit_decision(
        db, actor=royal, action="privileged.enrollment.confirm", outcome="success",
        reason="enrolled", ip=client_ip(request), device=_device(request)
    )
    return RecoveryOut(recovery_codes=codes)


@router.post("/reauthorize", response_model=TokenOut)
def reauthorize(
    payload: ReauthorizeIn,
    request: Request,
    royal: User = Depends(require_royal),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenOut:
    ip = client_ip(request)
    throttle = _throttle(settings)
    throttle_key = f"privileged|{royal.id}|{ip}"
    retry = throttle.retry_after(throttle_key, db=db)
    if retry:
        privileged_auth.audit_decision(
            db, actor=royal, action="privileged.reauthorize", outcome="denied",
            reason="locked", ip=ip, device=_device(request),
            operation_kind=payload.operation_kind, operation_id=payload.operation_id
        )
        raise HTTPException(status_code=429, detail="locked", headers={"Retry-After": str(retry)})
    try:
        token = privileged_auth.issue_reauthorization(
            db, royal, settings, session_token_hash=_session_hash(request, settings),
            password=payload.password, code=payload.code,
            operation_kind=payload.operation_kind, operation_id=payload.operation_id
        )
    except privileged_auth.PrivilegedAuthError as exc:
        throttle.register_failure(throttle_key, db=db)
        privileged_auth.audit_decision(
            db, actor=royal, action="privileged.reauthorize", outcome="denied",
            reason=str(exc), ip=ip, device=_device(request),
            operation_kind=payload.operation_kind, operation_id=payload.operation_id
        )
        detail = "privileged_enrollment_required" if str(exc) == "privileged_enrollment_required" else "invalid_credentials"
        raise HTTPException(status_code=409 if detail.endswith("required") else 401, detail=detail) from exc
    throttle.reset(throttle_key, db=db)
    privileged_auth.audit_decision(
        db, actor=royal, action="privileged.reauthorize", outcome="success",
        reason="issued", ip=ip, device=_device(request),
        operation_kind=payload.operation_kind, operation_id=payload.operation_id
    )
    return TokenOut(token=token, expires_in=privileged_auth.REAUTH_TTL_SECONDS)
