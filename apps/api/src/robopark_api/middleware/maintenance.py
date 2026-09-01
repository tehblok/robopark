"""Block non-operator sessions while a royal ops job is running."""

from __future__ import annotations

from http.cookies import SimpleCookie

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from robopark_api.security import hash_session_token
from robopark_api.services.ops.jobs import is_exempt_session, is_maintenance_active

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
        if path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        if (scope.get("method") or "").upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return
        app = scope.get("app")
        ops_dir = getattr(getattr(app, "state", None), "ops_dir", None)
        cookie_name = getattr(
            getattr(app, "state", None), "session_cookie_name", "robopark_session"
        )
        if ops_dir is None or not is_maintenance_active(ops_dir):
            await self.app(scope, receive, send)
            return
        token = _cookie_value(scope, cookie_name)
        token_hash = hash_session_token(token) if token else None
        if is_exempt_session(ops_dir, token_hash):
            await self.app(scope, receive, send)
            return
        response = JSONResponse({"detail": "maintenance"}, status_code=503)
        await response(scope, receive, send)
