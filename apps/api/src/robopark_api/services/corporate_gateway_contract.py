"""Strict typed contract for read-only corporate requests executed on an owner's Mac."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Literal

Service = Literal["inventory", "tracker"]
Action = Literal[
    "rover_card",
    "rover_installations",
    "issue_read",
    "issues_search",
    "comments_read",
    "transitions_read",
    "history_read",
    "queue_read",
    "queue_components_read",
    "issues_count",
    "user_read",
]

OPERATION = "corporate_read_v1"
MAX_REQUEST_BYTES = 8 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
DEADLINE_MS = 15_000

SERVICE_CAPABILITY = {
    "inventory": "corporate_inventory_read_v1",
    "tracker": "corporate_tracker_read_v1",
}
SERVICE_ACTIONS = {
    "inventory": frozenset({"rover_card", "rover_installations"}),
    "tracker": frozenset(
        {
            "issue_read",
            "issues_search",
            "comments_read",
            "transitions_read",
            "history_read",
            "queue_read",
            "queue_components_read",
            "issues_count",
            "user_read",
        }
    ),
}
_ISSUE = re.compile(r"[A-Z][A-Z0-9_]{0,63}-[1-9][0-9]{0,11}\Z")
_QUEUE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_ROVER = re.compile(r"a[0-9]{1,11}\Z")
_USER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PAGINATION_HEADERS = frozenset(
    {"x-total-count", "x-total-pages", "x-current-page", "x-next-page", "link"}
)
_TRACKER_PARAMS = {
    "issue_read": frozenset({"expand", "fields"}),
    "issues_search": frozenset({"page", "per_page", "expand", "fields", "query", "order"}),
    "comments_read": frozenset({"page", "per_page", "expand", "fields"}),
    "transitions_read": frozenset({"page", "per_page", "expand", "fields"}),
    "history_read": frozenset({"page", "per_page", "expand", "fields"}),
    "queue_read": frozenset({"expand", "fields"}),
    "queue_components_read": frozenset({"page", "per_page", "expand", "fields"}),
    "issues_count": frozenset({"query"}),
    "user_read": frozenset(),
}


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class CorporateReadResponse:
    status_code: int
    headers: dict[str, str]
    payload: dict | list


def _bounded_json(value: object, *, limit: int) -> int:
    nodes = 0
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > 20_000 or depth > 12:
            raise ContractError("corporate_payload_invalid")
        if isinstance(item, dict):
            if len(item) > 2_000 or not all(
                isinstance(key, str) and len(key) <= 128 for key in item
            ):
                raise ContractError("corporate_payload_invalid")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > 2_000:
                raise ContractError("corporate_payload_invalid")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > 128 * 1024:
                raise ContractError("corporate_payload_invalid")
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise ContractError("corporate_payload_invalid")
        elif item is not None and type(item) not in {bool, int}:
            raise ContractError("corporate_payload_invalid")
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ContractError("corporate_payload_invalid") from exc
    if len(encoded) > limit:
        raise ContractError("corporate_payload_too_large")
    return len(encoded)


def validate_request(service: str, action: str, resource: str, params: object) -> dict:
    if service not in SERVICE_ACTIONS or action not in SERVICE_ACTIONS[service]:
        raise ContractError("corporate_action_invalid")
    if not isinstance(params, dict):
        raise ContractError("corporate_params_invalid")
    if service == "inventory":
        if _ROVER.fullmatch(resource) is None:
            raise ContractError("corporate_resource_invalid")
        if action == "rover_card":
            if params:
                raise ContractError("corporate_params_invalid")
        else:
            if set(params) - {"offset", "limit", "slot"}:
                raise ContractError("corporate_params_invalid")
            if (
                type(params.get("offset", 0)) is not int
                or not 0 <= params.get("offset", 0) <= 100_000
            ):
                raise ContractError("corporate_params_invalid")
            if type(params.get("limit", 25)) is not int or not 1 <= params.get("limit", 25) <= 50:
                raise ContractError("corporate_params_invalid")
            slot = params.get("slot")
            if slot is not None and (
                not isinstance(slot, str) or re.fullmatch(r"/[a-zA-Z0-9_/-]{1,200}", slot) is None
            ):
                raise ContractError("corporate_params_invalid")
    else:
        if set(params) - _TRACKER_PARAMS[action]:
            raise ContractError("corporate_params_invalid")
        if action in {"issues_search", "issues_count"}:
            expected = "search" if action == "issues_search" else "count"
            if resource != expected:
                raise ContractError("corporate_resource_invalid")
            query = params.get("query")
            if not isinstance(query, str) or not 1 <= len(query) <= 4_096:
                raise ContractError("corporate_params_invalid")
        elif action == "user_read":
            if _USER.fullmatch(resource) is None:
                raise ContractError("corporate_resource_invalid")
        elif action in {"queue_read", "queue_components_read"}:
            if _QUEUE.fullmatch(resource) is None:
                raise ContractError("corporate_resource_invalid")
        elif _ISSUE.fullmatch(resource) is None:
            raise ContractError("corporate_resource_invalid")
        for key in ("page", "per_page"):
            if key in params and (
                type(params[key]) is not int
                or params[key] < 1
                or params[key] > (10_000 if key == "page" else 100)
            ):
                raise ContractError("corporate_params_invalid")
        for key in ("expand", "fields"):
            if key in params and (
                not isinstance(params[key], str) or not 1 <= len(params[key]) <= 1_024
            ):
                raise ContractError("corporate_params_invalid")
        if "order" in params and (
            not isinstance(params["order"], list)
            or len(params["order"]) > 8
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[-+]?[A-Za-z][A-Za-z0-9_]{0,63}", value) is None
                for value in params["order"]
            )
        ):
            raise ContractError("corporate_params_invalid")
    normalized = dict(params)
    _bounded_json(
        {"service": service, "action": action, "resource": resource, "params": normalized},
        limit=MAX_REQUEST_BYTES,
    )
    return normalized


def validate_result(
    result: object,
    *,
    service: str,
    action: str,
    resource: str,
    owner_user_id: int,
    owner_tracker_login: str,
) -> CorporateReadResponse:
    validated = validate_result_shape(result)
    if (
        validated["service"] != service
        or validated["action"] != action
        or validated["resource"] != resource
        or validated["owner_user_id"] != owner_user_id
        or validated["identity_login"].casefold() != owner_tracker_login.casefold()
    ):
        raise ContractError("corporate_response_invalid")
    return CorporateReadResponse(
        validated["status_code"], validated["headers"], validated["payload"]
    )


def validate_result_shape(result: object) -> dict:
    if not isinstance(result, dict) or set(result) != {
        "service",
        "action",
        "resource",
        "owner_user_id",
        "identity_login",
        "status_code",
        "headers",
        "payload",
    }:
        raise ContractError("corporate_response_invalid")
    if (
        result["service"] not in SERVICE_ACTIONS
        or result["action"] not in SERVICE_ACTIONS[result["service"]]
        or not isinstance(result["resource"], str)
        or type(result["owner_user_id"]) is not int
        or result["owner_user_id"] <= 0
        or not isinstance(result["identity_login"], str)
        or not result["identity_login"]
        or type(result["status_code"]) is not int
        or not 200 <= result["status_code"] <= 299
        or not isinstance(result["headers"], dict)
        or not isinstance(result["payload"], (dict, list))
    ):
        raise ContractError("corporate_response_invalid")
    headers: dict[str, str] = {}
    for key, value in result["headers"].items():
        if not isinstance(key, str) or key.lower() not in _PAGINATION_HEADERS:
            raise ContractError("corporate_response_invalid")
        if not isinstance(value, (str, int)) or len(str(value)) > 4_096:
            raise ContractError("corporate_response_invalid")
        headers[key.lower()] = str(value)
    _bounded_json(headers, limit=16 * 1024)
    _bounded_json(result["payload"], limit=MAX_RESPONSE_BYTES)
    return {**result, "headers": headers}
