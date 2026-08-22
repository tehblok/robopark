"""Emergency API HTTP client."""

from __future__ import annotations

from typing import Any

import httpx

EMERGENCY_BASE = "https://emergency.sdc.yandex-team.ru/api/v1"


class EmergencyAuthError(Exception):
    pass


class EmergencyError(Exception):
    pass


def fetch_robot_payload(*, cookie: str, vin: str) -> dict[str, Any]:
    headers = {"Cookie": cookie}
    url = f"{EMERGENCY_BASE}/{vin}/"
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise EmergencyError(str(exc)) from exc

    content_type = response.headers.get("content-type", "")
    if response.status_code == 401 or "text/html" in content_type.lower():
        raise EmergencyAuthError("emergency cookie invalid")
    if response.status_code >= 400:
        raise EmergencyError(f"emergency upstream status {response.status_code}")

    payload = response.json()
    if not isinstance(payload, dict):
        raise EmergencyError("unexpected emergency response")
    return payload
