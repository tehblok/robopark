"""Reject browser mutations from origins that cannot own this session."""

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def _origin(value: str, *, referrer: bool = False) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (not referrer and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment))
        ):
            return None
        return (
            parsed.scheme,
            parsed.hostname.lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except ValueError:
        return None


class BrowserOriginMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed_origins = {origin for value in allowed_origins if (origin := _origin(value))}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        allowed = self.allowed_origins | {_origin(str(request.url), referrer=True)}
        origin = request.headers.get("origin")
        referrer = request.headers.get("referer")
        if origin is not None:
            candidate = _origin(origin)
            denied = candidate is None or candidate not in allowed
        elif referrer is not None:
            candidate = _origin(referrer, referrer=True)
            denied = candidate is None or candidate not in allowed
        else:
            # Native clients and internal keyed requests have no browser headers.
            # Modern browser forms/fetch send Origin or Fetch Metadata; a sibling
            # subdomain is same-site, but is not a trusted application origin.
            denied = request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}
        if denied:
            response = JSONResponse(
                {"detail": "untrusted_origin"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
