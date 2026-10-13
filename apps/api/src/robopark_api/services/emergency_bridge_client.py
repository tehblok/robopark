from __future__ import annotations

import json
import secrets
import time

import httpx

from robopark_api.config import Settings, get_settings
from robopark_api.services.emergency_bridge_auth import body_signature


class BridgeError(RuntimeError):
    pass


def fetch_robot_payload(
    *,
    vin: str,
    requester_user_id: int,
    attempt_budget: int = 1,
    deadline_ms: int = 20_000,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    body = json.dumps(
        {
            "vin": vin,
            "requester_user_id": requester_user_id,
            "attempt_budget": attempt_budget,
            "deadline_ms": deadline_ms,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(24)
    headers = {
        "Content-Type": "application/json",
        "X-Robopark-Timestamp": timestamp,
        "X-Robopark-Nonce": nonce,
        "X-Robopark-Signature": body_signature(settings.secret_key, timestamp, nonce, body),
    }
    try:
        with httpx.Client(timeout=(deadline_ms / 1000) + 1) as client:
            response = client.post(
                settings.emergency_bridge_url.rstrip("/") + "/internal/v1/robot-payload",
                content=body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise BridgeError("bridge_unavailable") from exc
    if response.status_code != 200:
        raise BridgeError("bridge_request_failed")
    try:
        payload = response.json()["payload"]
    except (ValueError, KeyError, TypeError) as exc:
        raise BridgeError("bridge_invalid_response") from exc
    if not isinstance(payload, dict):
        raise BridgeError("bridge_invalid_response")
    return payload
