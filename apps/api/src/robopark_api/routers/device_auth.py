from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.models import AuthSession
from robopark_api.security import hash_session_token, new_session_token
from robopark_api.services import device_auth
from robopark_api.services.login_throttle import client_ip

router = APIRouter(prefix="/auth/device-auth", tags=["auth"])


class _RateLimiter:
    def __init__(self, limit: int, window_seconds: float, max_keys: int = 4096) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._items: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            for candidate, values in list(self._items.items()):
                while values and now - values[0] >= self.window_seconds:
                    values.popleft()
                if not values:
                    self._items.pop(candidate, None)
            if key not in self._items and len(self._items) >= self.max_keys:
                oldest = min(self._items, key=lambda item: self._items[item][-1])
                self._items.pop(oldest, None)
            values = self._items.setdefault(key, deque())
            if len(values) >= self.limit:
                return False
            values.append(now)
            return True


_ip_limiter = _RateLimiter(120, 60)


class ChallengeIn(BaseModel):
    device_id: UUID


class RenewIn(BaseModel):
    device_id: UUID
    challenge_id: UUID
    nonce: str = Field(min_length=32, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    signature: str = Field(min_length=64, max_length=256)


def _limit(request: Request) -> None:
    if not _ip_limiter.allow(client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="device_auth_rate_limited",
            headers={"Retry-After": "60"},
        )


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
        max_age=settings.session_absolute_ttl_seconds,
    )


@router.post("/challenge")
def challenge(
    payload: ChallengeIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    device_id = str(payload.device_id)
    _limit(request)
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    device_auth.charge_device_request(db, device_id, now=now)
    nonce = secrets.token_urlsafe(32)
    challenge_id = str(uuid4())
    row, _device = device_auth.issue_challenge(
        db,
        settings,
        device_id=device_id,
        nonce_hash=hashlib.sha256(nonce.encode()).hexdigest(),
        challenge_id=challenge_id,
        now=now,
    )
    return {
        "challenge_id": row.id,
        "nonce": nonce,
        "expires_at": device_auth.wire_time(row.expires_at),
        "instance_id": str(settings.robopark_instance_id),
        "environment": settings.robopark_environment,
    }


@router.post("/renew", status_code=status.HTTP_204_NO_CONTENT)
def renew(
    payload: RenewIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    device_id = str(payload.device_id)
    _limit(request)
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    device_auth.charge_device_request(db, device_id, now=now)
    device = device_auth.consume_challenge(
        db,
        settings,
        device_id=device_id,
        challenge_id=str(payload.challenge_id),
        nonce=payload.nonce,
        signature=payload.signature,
        now=now,
    )
    raw_token = new_session_token()
    db.add(
        AuthSession(
            user_id=device.owner_user_id,
            token_hash=hash_session_token(raw_token),
            expires_at=now + timedelta(seconds=settings.session_idle_seconds),
            auth_method=device_auth.AUTH_METHOD_DEVICE,
            device_id=device.id,
        )
    )
    device.last_seen_at = now
    db.commit()
    _set_cookie(response, settings, raw_token)
