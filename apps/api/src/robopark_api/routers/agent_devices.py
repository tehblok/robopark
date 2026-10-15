from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import stat
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_approved, require_emergency_viewer, require_royal
from robopark_api.models import AccessStatus, AgentDevice, AgentEnrollment, User
from robopark_api.services import (
    audit,
    emergency_bridge_client,
    gateway_policy,
    rbac,
    robot_registry,
)
from robopark_api.services.login_throttle import client_ip

router = APIRouter(prefix="/agent-devices", tags=["agent-devices"])
PROTOCOL = 1
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_RE = re.compile(
    r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}(?:-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){2}$"
)
_DEPLOYMENT_PROFILE = Path("/data/macos-deployment/profile.json")
_DEPLOYMENT_PROFILE_MAX_BYTES = 16 * 1024


class ClaimRateLimiter:
    def __init__(self, *, limit: int, window_seconds: float, max_clients: int = 4096) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            for key, values in list(self._attempts.items()):
                while values and now - values[0] >= self.window_seconds:
                    values.popleft()
                if not values:
                    self._attempts.pop(key, None)
            if client not in self._attempts and len(self._attempts) >= self.max_clients:
                oldest = min(self._attempts, key=lambda key: self._attempts[key][-1])
                self._attempts.pop(oldest, None)
            attempts = self._attempts.setdefault(client, deque())
            while attempts and now - attempts[0] >= self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.limit:
                return False
            attempts.append(now)
            return True


_claim_limiter = ClaimRateLimiter(limit=30, window_seconds=60)


class EnrollmentIn(BaseModel):
    label: str = Field(min_length=1, max_length=96)


class ClaimIn(BaseModel):
    code: str = Field(min_length=8, max_length=32)
    label: str = Field(min_length=1, max_length=96)
    public_key: str = Field(min_length=80, max_length=128)
    environment: Literal["test", "production"]
    instance_id: UUID


class AuthenticatedClaimIn(BaseModel):
    label: str = Field(min_length=1, max_length=96)
    public_key: str = Field(min_length=80, max_length=128)
    environment: Literal["test", "production"]
    instance_id: UUID


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _code_hash(code: str, settings: Settings) -> str:
    if not settings.secret_key:
        raise HTTPException(503, "secret_key_required")
    key = hmac.new(
        settings.secret_key.encode(), b"robopark-agent-enrollment-v1", hashlib.sha256
    ).digest()
    return hmac.new(key, code.encode("ascii"), hashlib.sha256).hexdigest()


def _new_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def _normalize_code(value: str) -> str:
    normalized = value.strip().upper()
    if _CODE_RE.fullmatch(normalized) is None:
        raise HTTPException(400, "invalid_code")
    return normalized


def _parse_public_key(value: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) != 65 or raw[0] != 4:
            raise ValueError
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "invalid_public_key") from exc
    return raw


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _signed_profile() -> dict | None:
    try:
        if not stat.S_ISREG(_DEPLOYMENT_PROFILE.lstat().st_mode):
            return None
        with _DEPLOYMENT_PROFILE.open("rb") as source:
            raw = source.read(_DEPLOYMENT_PROFILE_MAX_BYTES + 1)
        if not raw or len(raw) > _DEPLOYMENT_PROFILE_MAX_BYTES:
            return None
        envelope = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"payload", "signature"}
        or not isinstance(envelope["payload"], str)
        or not isinstance(envelope["signature"], str)
    ):
        return None
    return envelope


@router.get("/info")
def info(response: Response, settings: Settings = Depends(get_settings)) -> dict:
    _no_store(response)
    return {
        "environment": settings.robopark_environment,
        "instance_id": str(settings.robopark_instance_id),
        "protocol": PROTOCOL,
        "claim_url": "/api/agent-devices/claim",
        "websocket_url": "/api/emergency/bridge/ws",
        "compute_websocket_url": "/api/emergency/bridge/compute/ws",
        "signed_profile": _signed_profile(),
    }


@router.get("/probe-target")
def probe_target(
    response: Response,
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
) -> dict:
    """Choose a real authorized robot; the Mac performs the actual network probe.

    Registry discovery shares Tracker's cache/singleflight and never reads
    Emergency on the server. Missing configuration or an empty roster cannot
    manufacture a healthy Mac or an arbitrary VIN.
    """
    _no_store(response)
    roster = robot_registry.registry_rows(
        db,
        user,
        park_id=None,
        query="",
        state="all",
        active_errors=False,
        open_tasks=False,
        offset=0,
        limit=1,
    )
    items = roster["items"]
    return {"vin": items[0]["vin"] if items else None}


@router.get("/setup", response_class=HTMLResponse)
def setup_page() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robopark Mac agent</title>
<main><h1>Подключение Mac</h1><p id="status">Проверяем доступ…</p>
<p><a id="login" href="/login" hidden>Войти</a></p>
<section id="controls" hidden><label>Название <input id="label" maxlength="96"
value="Mac"></label> <button id="create" type="button">Создать код</button>
<pre id="result" aria-live="polite"></pre><h2>Устройства</h2><ul id="devices"></ul></section>
</main><script src="/api/agent-devices/setup.js" defer></script></html>""",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/setup.js")
def setup_script() -> Response:
    script = r"""
const statusNode = document.getElementById('status');
const controls = document.getElementById('controls');
const login = document.getElementById('login');
const result = document.getElementById('result');
const devices = document.getElementById('devices');
const api = '/api/agent-devices';
let canApprove = false;

function showError(response) {
  statusNode.textContent = response.status === 403
    ? 'Нет доступа к Emergency. Обратитесь к администратору.'
    : 'Не удалось загрузить данные.';
}

async function loadDevices() {
  const response = await fetch(api + (canApprove ? '?all=true' : ''), {credentials: 'same-origin'});
  if (!response.ok) { showError(response); return; }
  const body = await response.json();
  devices.replaceChildren();
  for (const device of body.devices) {
    const item = document.createElement('li');
    const owner = device.owner_username ? ' (' + device.owner_username + ')' : '';
    item.append(document.createTextNode(device.label + owner + ' '));
    const revoke = document.createElement('button');
    revoke.type = 'button'; revoke.textContent = 'Отозвать';
    revoke.disabled = Boolean(device.revoked_at);
    revoke.addEventListener('click', async () => {
      if (!window.confirm('Отозвать устройство? Для возврата потребуется повторная регистрация.')) return;
      const response = await fetch(api + '/' + encodeURIComponent(device.id), {
        method: 'DELETE', credentials: 'same-origin'
      });
      if (response.ok) await loadDevices(); else showError(response);
    });
    item.append(revoke);
    item.append(document.createTextNode(' Статус: ' + device.status + ' '));
    devices.append(item);
  }
}

document.getElementById('create').addEventListener('click', async () => {
  result.textContent = 'Создаём…';
  const response = await fetch(api + '/enrollments', {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({label: document.getElementById('label').value || 'Mac'})
  });
  if (!response.ok) { result.textContent = ''; showError(response); return; }
  const body = await response.json();
  result.textContent = body.code + '\nКод действует до ' + body.expires_at;
});

(async () => {
  const auth = await fetch('/api/auth/me', {credentials: 'same-origin'});
  if (auth.status === 401) {
    statusNode.textContent = 'Войдите, чтобы подключить Mac.'; login.hidden = false; return;
  }
  if (!auth.ok) { showError(auth); return; }
  const me = await auth.json(); canApprove = me.role === 'admin' || me.role === 'royal';
  statusNode.textContent = 'Доступ проверен.'; controls.hidden = false;
  await loadDevices();
})();
"""
    return Response(
        content=script,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/enrollments", status_code=status.HTTP_201_CREATED)
def create_enrollment(
    payload: EnrollmentIn,
    response: Response,
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _no_store(response)
    now = datetime.now(UTC)
    db.execute(
        update(AgentEnrollment)
        .where(AgentEnrollment.owner_user_id == user.id, AgentEnrollment.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = _new_code()
    expires_at = now + timedelta(minutes=5)
    db.add(
        AgentEnrollment(
            owner_user_id=user.id,
            code_hash=_code_hash(code, settings),
            label=payload.label.strip(),
            environment=settings.robopark_environment,
            instance_id=str(settings.robopark_instance_id),
            expires_at=expires_at,
        )
    )
    db.commit()
    return {
        "code": code,
        "expires_at": expires_at.isoformat(),
        "environment": settings.robopark_environment,
        "instance_id": str(settings.robopark_instance_id),
    }


@router.post("/claim", status_code=status.HTTP_201_CREATED)
def claim(
    request: Request,
    payload: ClaimIn,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _no_store(response)
    if not _claim_limiter.allow(client_ip(request)):
        raise HTTPException(429, "claim_rate_limited")
    if (
        payload.environment != settings.robopark_environment
        or payload.instance_id != settings.robopark_instance_id
    ):
        raise HTTPException(400, "environment_mismatch")
    public_key = _parse_public_key(payload.public_key)
    now = datetime.now(UTC)
    enrollment = db.scalar(
        select(AgentEnrollment).where(
            AgentEnrollment.code_hash == _code_hash(_normalize_code(payload.code), settings)
        )
    )
    if enrollment is None or _aware(enrollment.expires_at) <= now:
        raise HTTPException(409, "invalid_code")
    owner = db.scalar(
        select(User).where(User.id == enrollment.owner_user_id).with_for_update(of=User)
    )
    if (
        owner is None
        or not owner.is_active
        or owner.access_status != AccessStatus.approved.value
        or not rbac.has_permission(db, owner, rbac.PERMISSION_NAV_EMERGENCY)
    ):
        raise HTTPException(409, "invalid_code")
    claimed = db.execute(
        update(AgentEnrollment)
        .where(AgentEnrollment.id == enrollment.id, AgentEnrollment.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "invalid_code")
    existing = db.scalar(
        select(AgentDevice).where(
            AgentDevice.public_key_x963 == public_key,
            AgentDevice.environment == enrollment.environment,
            AgentDevice.instance_id == enrollment.instance_id,
        )
    )
    if existing is not None:
        if existing.owner_user_id != enrollment.owner_user_id or existing.revoked_at is not None:
            db.rollback()
            raise HTTPException(409, "invalid_code")
        db.commit()
        return {
            "device_id": existing.id,
            "owner_user_id": existing.owner_user_id,
            "owner_tracker_login": gateway_policy.corporate_owner_login(owner),
            "environment": existing.environment,
            "instance_id": existing.instance_id,
            "protocol": PROTOCOL,
            "status": "approved",
        }
    active_count = db.scalar(
        select(func.count())
        .select_from(AgentDevice)
        .where(
            AgentDevice.owner_user_id == enrollment.owner_user_id,
            AgentDevice.revoked_at.is_(None),
        )
    )
    if int(active_count or 0) >= settings.agent_device_max_per_user:
        db.rollback()
        raise HTTPException(409, "device_limit")
    device = AgentDevice(
        owner_user_id=enrollment.owner_user_id,
        label=payload.label.strip(),
        public_key_x963=public_key,
        environment=enrollment.environment,
        instance_id=enrollment.instance_id,
        approved_at=now,
        approved_by_user_id=enrollment.owner_user_id,
    )
    db.add(device)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = db.scalar(
            select(AgentDevice).where(
                AgentDevice.public_key_x963 == public_key,
                AgentDevice.environment == enrollment.environment,
                AgentDevice.instance_id == enrollment.instance_id,
            )
        )
        if (
            raced is None
            or raced.owner_user_id != enrollment.owner_user_id
            or raced.revoked_at is not None
        ):
            raise HTTPException(409, "invalid_code") from None
        consumed = db.execute(
            update(AgentEnrollment)
            .where(
                AgentEnrollment.id == enrollment.id,
                AgentEnrollment.consumed_at.is_(None),
            )
            .values(consumed_at=now)
        )
        if consumed.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "invalid_code") from None
        db.commit()
        return {
            "device_id": raced.id,
            "owner_user_id": raced.owner_user_id,
            "owner_tracker_login": gateway_policy.corporate_owner_login(owner),
            "environment": raced.environment,
            "instance_id": raced.instance_id,
            "protocol": PROTOCOL,
            "status": "approved",
        }
    db.refresh(device)
    audit.record(
        db,
        action=audit.ACTION_AGENT_DEVICE_ENROLLED,
        actor=owner,
        target_type="agent_device",
        target_id=device.id,
        detail=json.dumps(
            {
                "environment": device.environment,
                "instance_id": device.instance_id,
                "label": device.label,
            },
            sort_keys=True,
        ),
    )
    return {
        "device_id": device.id,
        "owner_user_id": device.owner_user_id,
        "owner_tracker_login": gateway_policy.corporate_owner_login(owner),
        "environment": device.environment,
        "instance_id": device.instance_id,
        "protocol": PROTOCOL,
        "status": "approved",
    }


def _authenticated_device_payload(device: AgentDevice, owner: User) -> dict:
    return {
        "device_id": device.id,
        "owner_user_id": device.owner_user_id,
        "owner_tracker_login": gateway_policy.corporate_owner_login(owner),
        "environment": device.environment,
        "instance_id": device.instance_id,
        "protocol": PROTOCOL,
        "status": "approved",
    }


@router.post("/claim-auth", status_code=status.HTTP_201_CREATED)
def claim_authenticated(
    payload: AuthenticatedClaimIn,
    response: Response,
    user: User = Depends(require_approved),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Bind one installation key to the first approved account that presents it."""
    _no_store(response)
    if (
        payload.environment != settings.robopark_environment
        or payload.instance_id != settings.robopark_instance_id
    ):
        raise HTTPException(400, "environment_mismatch")
    public_key = _parse_public_key(payload.public_key)
    # Serialize the owner's count + insert across concurrent first logins and
    # the code-based enrollment path. Re-read authorization under the lock.
    owner = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update(of=User)
        .execution_options(populate_existing=True)
    )
    if owner is None or not owner.is_active or owner.access_status != AccessStatus.approved.value:
        raise HTTPException(403, "device_enrollment_forbidden")
    existing = db.scalar(
        select(AgentDevice).where(
            AgentDevice.public_key_x963 == public_key,
            AgentDevice.environment == payload.environment,
            AgentDevice.instance_id == str(payload.instance_id),
        )
    )
    if existing is not None:
        if existing.owner_user_id != user.id:
            raise HTTPException(409, "device_owner_mismatch")
        if existing.revoked_at is not None:
            raise HTTPException(409, "device_revoked")
        response.status_code = status.HTTP_200_OK
        return _authenticated_device_payload(existing, owner)

    active_count = db.scalar(
        select(func.count())
        .select_from(AgentDevice)
        .where(AgentDevice.owner_user_id == user.id, AgentDevice.revoked_at.is_(None))
    )
    if int(active_count or 0) >= settings.agent_device_max_per_user:
        raise HTTPException(409, "device_limit")
    now = datetime.now(UTC)
    device = AgentDevice(
        owner_user_id=user.id,
        label=payload.label.strip(),
        public_key_x963=public_key,
        environment=payload.environment,
        instance_id=str(payload.instance_id),
        approved_at=now,
        approved_by_user_id=user.id,
    )
    db.add(device)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = db.scalar(
            select(AgentDevice).where(
                AgentDevice.public_key_x963 == public_key,
                AgentDevice.environment == payload.environment,
                AgentDevice.instance_id == str(payload.instance_id),
            )
        )
        if raced is None:
            raise HTTPException(409, "device_identity_conflict") from None
        if raced.owner_user_id != user.id:
            raise HTTPException(409, "device_owner_mismatch") from None
        if raced.revoked_at is not None:
            raise HTTPException(409, "device_revoked") from None
        response.status_code = status.HTTP_200_OK
        return _authenticated_device_payload(raced, owner)
    db.refresh(device)
    audit.record(
        db,
        action=audit.ACTION_AGENT_DEVICE_ENROLLED,
        actor=user,
        target_type="agent_device",
        target_id=device.id,
        detail=json.dumps(
            {
                "environment": device.environment,
                "instance_id": device.instance_id,
                "label": device.label,
                "method": "authenticated_first_login",
            },
            sort_keys=True,
        ),
    )
    return _authenticated_device_payload(device, owner)


def _device_json(
    device: AgentDevice, *, include_owner: bool = False, owner_username: str | None = None
) -> dict:
    item = {
        "id": device.id,
        "label": device.label,
        "environment": device.environment,
        "created_at": device.created_at.isoformat(),
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "approved_at": device.approved_at.isoformat() if device.approved_at else None,
        "approved_by_user_id": device.approved_by_user_id,
        "revoked_at": device.revoked_at.isoformat() if device.revoked_at else None,
        "status": ("revoked" if device.revoked_at is not None else "approved"),
    }
    if include_owner:
        item["owner_user_id"] = device.owner_user_id
        item["owner_username"] = owner_username
    return item


@router.post("/{device_id}/approve")
def approve_device(
    device_id: UUID,
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
) -> dict:
    if not rbac.is_admin_or_royal(user):
        raise HTTPException(403, "admin_required")
    device = db.get(AgentDevice, str(device_id))
    if device is None:
        raise HTTPException(404, "device_not_found")
    if device.revoked_at is not None:
        raise HTTPException(409, "device_revoked")
    if device.approved_at is None:
        device.approved_at = datetime.now(UTC)
        device.approved_by_user_id = user.id
        db.commit()
        db.refresh(device)
        audit.record(
            db,
            action=audit.ACTION_AGENT_DEVICE_APPROVED,
            actor=user,
            target_type="agent_device",
            target_id=device.id,
            detail=json.dumps(
                {"owner_user_id": device.owner_user_id, "label": device.label},
                sort_keys=True,
            ),
        )
    return _device_json(device, include_owner=True, owner_username=None)


@router.get("")
def list_devices(
    all_devices: bool = Query(default=False, alias="all"),
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
) -> dict:
    if all_devices and not rbac.is_royal(user):
        raise HTTPException(403, "royal_only")
    include_all = all_devices
    query = select(AgentDevice).order_by(AgentDevice.created_at, AgentDevice.id)
    if not include_all:
        query = query.where(AgentDevice.owner_user_id == user.id)
    devices = list(db.scalars(query).all())
    return {
        "devices": [
            _device_json(
                item,
                include_owner=include_all,
                owner_username=db.get(User, item.owner_user_id).username if include_all else None,
            )
            for item in devices
        ]
    }


@router.get("/pool-status")
def pool_status(
    user: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.emergency_bridge_enabled:
        return {
            "enabled": False,
            "scope": "all",
            "summary": dict.fromkeys(
                (
                    "connected",
                    "ready",
                    "busy",
                    "paused",
                    "capacity",
                    "inflight",
                    "queued",
                    "requests",
                    "completed",
                    "failed",
                    "encoded_bytes",
                    "decoded_bytes",
                    "probes_sent",
                    "probes_failed",
                    "upstream_dispatches",
                    "upstream_backpressure",
                ),
                0,
            ),
            "devices": [],
        }
    try:
        return emergency_bridge_client.fetch_pool_status(
            requester_user_id=user.id, settings=settings
        )
    except emergency_bridge_client.BridgeError as exc:
        raise HTTPException(503, "bridge_unavailable") from exc


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(
    device_id: UUID,
    user: User = Depends(require_emergency_viewer),
    db: Session = Depends(get_db),
) -> Response:
    device = db.get(AgentDevice, str(device_id))
    if device is None or (device.owner_user_id != user.id and not rbac.is_admin_or_royal(user)):
        raise HTTPException(404)
    if device.revoked_at is None:
        device.revoked_at = datetime.now(UTC)
        db.commit()
        audit.record(
            db,
            action=audit.ACTION_AGENT_DEVICE_REVOKED,
            actor=user,
            target_type="agent_device",
            target_id=device.id,
        )
    return Response(status_code=204)
