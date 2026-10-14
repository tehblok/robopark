from __future__ import annotations

import base64
import hashlib
import json

from robopark_api.config import Settings, get_settings
from robopark_api.services.emergency_bridge_client import BridgeError, _signed_post


def dispatch_compute(
    operation: str,
    payload: dict,
    requester_user_id: int,
    deadline_ms: int = 20_000,
    *,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    raw_payload = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    body = json.dumps(
        {
            "operation": operation,
            "payload_base64": base64.b64encode(raw_payload).decode(),
            "payload_sha256": hashlib.sha256(raw_payload).hexdigest(),
            "requester_user_id": requester_user_id,
            "deadline_ms": deadline_ms,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    response = _signed_post(
        "/internal/v1/compute",
        body,
        settings=settings,
        timeout_seconds=deadline_ms / 1000 + 1,
    )
    if response.status_code != 200:
        raise BridgeError("compute_unavailable")
    try:
        envelope = response.json()
        result = envelope["result"]
        device_id = envelope["device_id"]
    except (ValueError, KeyError, TypeError) as exc:
        raise BridgeError("compute_invalid_response") from exc
    if not isinstance(result, dict) or not isinstance(device_id, str):
        raise BridgeError("compute_invalid_response")
    return {**result, "device_id": device_id}


def fetch_compute_status(*, requester_user_id: int, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    body = json.dumps(
        {"requester_user_id": requester_user_id}, separators=(",", ":"), sort_keys=True
    ).encode()
    response = _signed_post(
        "/internal/v1/compute-status", body, settings=settings, timeout_seconds=3.0
    )
    if response.status_code != 200:
        raise BridgeError("compute_unavailable")
    try:
        result = response.json()
    except ValueError as exc:
        raise BridgeError("compute_invalid_response") from exc
    if not isinstance(result, dict) or not isinstance(result.get("devices"), list):
        raise BridgeError("compute_invalid_response")
    return result


def dispatch_direct(
    *,
    operation: str,
    payload_sha256: str,
    payload_bytes: int,
    offer: dict,
    requester_user_id: int,
    deadline_ms: int = 20_000,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    body = json.dumps(
        {
            "operation": operation,
            "payload_sha256": payload_sha256,
            "payload_bytes": payload_bytes,
            "offer": offer,
            "requester_user_id": requester_user_id,
            "deadline_ms": deadline_ms,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if len(body) > 80 * 1024:
        raise BridgeError("direct_request_too_large")
    response = _signed_post(
        "/internal/v1/compute-direct",
        body,
        settings=settings,
        timeout_seconds=deadline_ms / 1000 + 1,
    )
    if response.status_code != 200:
        raise BridgeError("compute_unavailable")
    try:
        result = response.json()
    except ValueError as exc:
        raise BridgeError("compute_invalid_response") from exc
    required = {
        "generation",
        "job_id",
        "attempt_id",
        "payload_sha256",
        "token",
        "answer",
        "device_id",
        "timeout_ms",
    }
    if not isinstance(result, dict) or not required.issubset(result):
        raise BridgeError("compute_invalid_response")
    return result


def cancel_direct(*, job_id: str, requester_user_id: int, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    body = json.dumps(
        {"job_id": job_id, "requester_user_id": requester_user_id},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    response = _signed_post(
        "/internal/v1/compute-direct-cancel", body, settings=settings, timeout_seconds=3.0
    )
    if response.status_code != 200:
        raise BridgeError("compute_cancel_failed")


def finish_direct(
    *,
    job_id: str,
    requester_user_id: int,
    outcome: str,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    body = json.dumps(
        {"job_id": job_id, "requester_user_id": requester_user_id, "outcome": outcome},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    response = _signed_post(
        "/internal/v1/compute-direct-finish", body, settings=settings, timeout_seconds=3.0
    )
    if response.status_code != 200:
        raise BridgeError("compute_finish_failed")
    try:
        result = response.json()
    except ValueError as exc:
        raise BridgeError("compute_invalid_response") from exc
    if not isinstance(result, dict) or result.get("status") != "accepted":
        raise BridgeError("compute_invalid_response")
    return result
