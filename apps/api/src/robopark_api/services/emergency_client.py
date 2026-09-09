"""Emergency API HTTP client."""

from __future__ import annotations

from typing import Any

import httpx

EMERGENCY_BASE = "https://emergency.sdc.yandex-team.ru/api/v1"
_BROWSER_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 Robopark/1"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


class EmergencyAuthError(Exception):
    pass


class EmergencyError(Exception):
    pass


def _cookie_candidates(cookie: str) -> list[str]:
    cookie = cookie.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    candidates = [cookie]
    pairs = [part.strip() for part in cookie.split(";") if "=" in part]
    for name in ("session_id", "sessionid2"):
        candidate = next(
            (pair for pair in pairs if pair.split("=", 1)[0].strip().casefold() == name),
            None,
        )
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _auth_response(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "")
    if response.status_code in {401, 403}:
        return True
    if "text/html" not in content_type.lower():
        return False
    body_hint = str(getattr(response, "text", "") or "")[:4096].casefold()
    return any(
        marker in body_hint
        for marker in ("passport.yandex", "oauth", "login", "войти", "авторизац")
    )


def fetch_robot_payload(*, cookie: str, vin: str) -> dict[str, Any]:
    url = f"{EMERGENCY_BASE}/{vin}/"
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for candidate in _cookie_candidates(cookie):
                response = client.get(url, headers={**_BROWSER_HEADERS, "Cookie": candidate})
                if not _auth_response(response):
                    break
            else:
                raise EmergencyAuthError("emergency cookie invalid")
    except httpx.HTTPError as exc:
        raise EmergencyError(str(exc)) from exc

    content_type = response.headers.get("content-type", "")
    html = "text/html" in content_type.lower()
    if html:
        raise EmergencyError("unexpected html response")
    if response.status_code >= 400:
        raise EmergencyError(f"emergency upstream status {response.status_code}")

    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise EmergencyError("invalid emergency response") from exc
    if not isinstance(payload, dict):
        raise EmergencyError("unexpected emergency response")
    return payload
