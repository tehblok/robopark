"""Signed API→bridge client for typed owner-Mac corporate reads."""

from __future__ import annotations

import json

from robopark_api.config import Settings, get_settings
from robopark_api.services.corporate_gateway_contract import (
    DEADLINE_MS,
    MAX_REQUEST_BYTES,
    ContractError,
    CorporateReadResponse,
    validate_request,
)
from robopark_api.services.emergency_bridge_client import BridgeError, _signed_post


class CorporateGatewayError(BridgeError):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def enabled(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return bool(
        settings.mac_corporate_gateway_enabled
        and settings.robopark_environment == "test"
        and settings.emergency_bridge_enabled
    )


def read(
    *,
    service: str,
    action: str,
    resource: str,
    params: dict,
    requester_user_id: int,
    park_id: int | None = None,
    deadline_ms: int = DEADLINE_MS,
    settings: Settings | None = None,
) -> CorporateReadResponse:
    settings = settings or get_settings()
    if not enabled(settings):
        raise CorporateGatewayError(503, "corporate_gateway_disabled")
    try:
        normalized = validate_request(service, action, resource, params)
    except ContractError as exc:
        raise CorporateGatewayError(422, str(exc)) from exc
    if not 1 <= deadline_ms <= DEADLINE_MS:
        raise CorporateGatewayError(422, "corporate_deadline_invalid")
    body = json.dumps(
        {
            "service": service,
            "action": action,
            "resource": resource,
            "params": normalized,
            "requester_user_id": requester_user_id,
            "park_id": park_id,
            "deadline_ms": deadline_ms,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise CorporateGatewayError(422, "corporate_request_too_large")
    try:
        response = _signed_post(
            "/internal/v1/corporate-read",
            body,
            settings=settings,
            timeout_seconds=deadline_ms / 1000 + 1,
        )
    except BridgeError as exc:
        raise CorporateGatewayError(503, "corporate_unavailable") from exc
    if response.status_code != 200:
        safe_status = response.status_code if response.status_code in {401, 403, 404, 429} else 503
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        safe_codes = {
            "corporate_authentication",
            "corporate_access_denied",
            "corporate_not_found",
            "corporate_rate_limited",
            "corporate_unavailable",
            "corporate_gateway_disabled",
        }
        code = (
            detail if isinstance(detail, str) and detail in safe_codes else "corporate_unavailable"
        )
        raise CorporateGatewayError(safe_status, code)
    try:
        value = response.json()
        status_code = value["status_code"]
        headers = value["headers"]
        payload = value["payload"]
    except (ValueError, KeyError, TypeError) as exc:
        raise CorporateGatewayError(503, "corporate_invalid_response") from exc
    if (
        type(status_code) is not int
        or not 200 <= status_code <= 299
        or not isinstance(headers, dict)
        or not all(isinstance(key, str) and isinstance(item, str) for key, item in headers.items())
        or not isinstance(payload, (dict, list))
    ):
        raise CorporateGatewayError(503, "corporate_invalid_response")
    return CorporateReadResponse(status_code, headers, payload)
