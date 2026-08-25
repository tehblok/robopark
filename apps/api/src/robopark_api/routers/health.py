"""Liveness and readiness endpoints.

``/health`` returned a static ``{"status": "ok"}`` and therefore reported a
healthy API even when the database was unreachable. It is now split:

* ``/health``  — liveness: the process is up (cheap, no dependencies).
* ``/health/ready`` — readiness: the database answers and integrations are
  configured. Returns 503 when a hard dependency is broken.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.services import platform_settings as settings_svc

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe: intentionally dependency-free."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(response: Response, db: Session = Depends(get_db)) -> dict:
    """Readiness probe used by Docker/Compose healthchecks."""
    checks: dict[str, str] = {}
    ready = True

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Readiness: database check failed")
        checks["database"] = f"error: {type(exc).__name__}"
        ready = False

    # Integrations are reported but do not fail readiness: the UI stays usable
    # (parks, reports, admin) while a token is being re-issued.
    try:
        checks["tracker_token"] = "configured" if settings_svc.get_tracker_token(db) else "missing"
        cookie_valid = settings_svc.get_emergency_cookie_valid(db)
        if not settings_svc.get_emergency_cookie(db):
            checks["emergency_cookie"] = "missing"
        elif cookie_valid is False:
            checks["emergency_cookie"] = "invalid"
        else:
            checks["emergency_cookie"] = "ok"
    except Exception:  # noqa: BLE001
        logger.exception("Readiness: integration check failed")
        checks["integrations"] = "error"

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if ready else "degraded", "checks": checks}
