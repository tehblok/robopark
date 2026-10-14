from __future__ import annotations

import base64
import hashlib
import json

from robopark_api.config import Settings, get_settings
from robopark_api.services.emergency_bridge_client import BridgeError, _signed_post


class RelayAdmissionError(BridgeError):
    def __init__(self, detail: str, *, retry_after_ms: int = 1000) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after_ms = retry_after_ms


class RelayTicketError(BridgeError):
    def __init__(self, detail: str, *, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def dispatch_compute(
    operation: str,
    payload: dict,
    requester_user_id: int,
    deadline_ms: int = 20_000,
    *,
    relay_ticket: str | None = None,
    raw_payload: bytes | None = None,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    raw_payload = (
        raw_payload
        or json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    )
    envelope = {
        "operation": operation,
        "payload_base64": base64.b64encode(raw_payload).decode(),
        "payload_sha256": hashlib.sha256(raw_payload).hexdigest(),
        "requester_user_id": requester_user_id,
        "deadline_ms": deadline_ms,
    }
    if relay_ticket is not None:
        envelope["relay_ticket"] = relay_ticket
    body = json.dumps(
        envelope,
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


def _relay_error(response, default: str) -> tuple[str, int]:
    try:
        value = response.json()
    except ValueError:
        return default, 1000
    if not isinstance(value, dict):
        return default, 1000
    detail = value.get("detail")
    retry_after_ms = value.get("retry_after_ms", 1000)
    return (
        detail if isinstance(detail, str) else default,
        retry_after_ms if type(retry_after_ms) is int and retry_after_ms >= 0 else 1000,
    )


def admit_relay(
    *,
    operation: str,
    payload_bytes: int,
    payload_sha256: str,
    requester_user_id: int,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    body = json.dumps(
        {
            "operation": operation,
            "payload_bytes": payload_bytes,
            "payload_sha256": payload_sha256,
            "requester_user_id": requester_user_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    response = _signed_post(
        "/internal/v1/compute-relay-admit", body, settings=settings, timeout_seconds=3.0
    )
    if response.status_code != 200:
        detail, retry_after_ms = _relay_error(response, "relay_unavailable")
        raise RelayAdmissionError(detail, retry_after_ms=retry_after_ms)
    try:
        result = response.json()
    except ValueError as exc:
        raise BridgeError("compute_invalid_response") from exc
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("ticket"), str)
        or type(result.get("expires_in_ms")) is not int
    ):
        raise BridgeError("compute_invalid_response")
    return result


def claim_relay(*, ticket: str, requester_user_id: int, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    body = json.dumps(
        {"requester_user_id": requester_user_id, "ticket": ticket},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    response = _signed_post(
        "/internal/v1/compute-relay-claim", body, settings=settings, timeout_seconds=3.0
    )
    if response.status_code != 200:
        detail, _ = _relay_error(response, "relay_ticket_invalid")
        raise RelayTicketError(detail, status_code=response.status_code)
    try:
        result = response.json()
    except ValueError as exc:
        raise BridgeError("compute_invalid_response") from exc
    if (
        not isinstance(result, dict)
        or result.get("ticket") != ticket
        or type(result.get("payload_bytes")) is not int
        or not isinstance(result.get("payload_sha256"), str)
    ):
        raise BridgeError("compute_invalid_response")
    return result


def cancel_relay(*, ticket: str, requester_user_id: int, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    body = json.dumps(
        {"requester_user_id": requester_user_id, "ticket": ticket},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    response = _signed_post(
        "/internal/v1/compute-relay-cancel", body, settings=settings, timeout_seconds=3.0
    )
    if response.status_code not in {200, 204}:
        detail, _ = _relay_error(response, "relay_cancel_failed")
        raise RelayTicketError(detail, status_code=response.status_code)


def abort_relay(*, ticket: str, requester_user_id: int, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    body = json.dumps(
        {"requester_user_id": requester_user_id, "ticket": ticket},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    response = _signed_post(
        "/internal/v1/compute-relay-abort", body, settings=settings, timeout_seconds=3.0
    )
    if response.status_code not in {200, 204}:
        detail, _ = _relay_error(response, "relay_abort_failed")
        raise RelayTicketError(detail, status_code=response.status_code)


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
