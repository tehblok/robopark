"""Block non-operator sessions while a royal ops job is running."""

from __future__ import annotations

from http.cookies import SimpleCookie

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from robopark_api.security import hash_session_token
from robopark_api.services.ops.jobs import is_exempt_session, is_maintenance_active
from robopark_api.services.ops.maintenance import HostMaintenanceActive, host_maintenance_active

EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/health/ready",
        "/ops/maintenance",
        # Royal force-clear must work even when the starter session is gone.
        "/admin/ops/abort",
    }
)


def _header_map(scope: Scope) -> dict[bytes, bytes]:
    return dict(scope.get("headers") or [])


def _cookie_value(scope: Scope, name: str) -> str | None:
    raw = _header_map(scope).get(b"cookie", b"").decode("latin-1")
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except (TypeError, ValueError):
        return None
    morsel = jar.get(name)
    return morsel.value if morsel is not None else None


class MaintenanceGateMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        method = (scope.get("method") or "").upper()
        app = scope.get("app")
        state = getattr(app, "state", None)
        settings = getattr(state, "ops_settings", None)
        ops_dir = getattr(state, "ops_dir", None)
        cookie_name = getattr(state, "session_cookie_name", "robopark_session")
        host_active = settings is not None and host_maintenance_active(settings)
        safe_poll = method in {"GET", "HEAD"} and path in {
            # Identity bootstrap is read-only; require_user skips session sliding
            # while the host marker is active. This never exempts Royal writes.
            "/auth/me",
            "/health",
            "/health/ready",
            "/ops/maintenance",
            "/admin/ops/job",
            "/admin/ops/system-health",
            "/admin/ops/artifact",
            "/admin/ops/diagnostic-artifact",
        }
        if host_active:
            if not safe_poll and method != "OPTIONS":
                await JSONResponse({"detail": "maintenance"}, status_code=503)(scope, receive, send)
                return
        elif (
            not (path in EXEMPT_PATHS or path.startswith("/admin/ops/") or method == "OPTIONS")
            and ops_dir is not None
            and is_maintenance_active(ops_dir)
        ):
            token = _cookie_value(scope, cookie_name)
            token_hash = hash_session_token(token) if token else None
            if not is_exempt_session(ops_dir, token_hash):
                await JSONResponse({"detail": "maintenance"}, status_code=503)(scope, receive, send)
                return
        try:
            await self.app(scope, receive, send)
        except HostMaintenanceActive:
            await JSONResponse({"detail": "maintenance"}, status_code=503)(scope, receive, send)
