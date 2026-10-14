"""Closed, bounded contract for owner-bound Tracker writes."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass

OPERATION = "corporate_write_v1"
SERVICE_CAPABILITY = "corporate_tracker_write_v1"
MAX_NATIVE_ATTACHMENT_BYTES = 4 * 1024 * 1024
MAX_SERVER_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_REQUEST_BYTES = 6 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024
DEADLINE_MS = 15_000
ACTIONS = frozenset({"comment_create", "issue_patch", "transition_execute", "attachment_upload"})

_ISSUE = re.compile(r"[A-Z][A-Z0-9_]{0,63}-[1-9][0-9]{0,11}\Z")
_TRANSITION = re.compile(r"[0-9A-Za-z_-]{1,128}\Z")
_STEP = re.compile(r"[a-z0-9_:-]{1,64}\Z")
_PATCH_FIELDS = frozenset(
    {
        "assignee",
        "tags",
        "components",
        "60df26695151a36df681d67b--theDefectCode",
        "solutionMethod",
    }
)
_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic", "image/heif"}
)


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class CorporateWriteResponse:
    status_code: int
    payload: dict


def _short_string(value: object, maximum: int, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str) and (allow_empty or bool(value)) and len(value.encode()) <= maximum
    )


def _string_list(value: object, *, maximum: int = 64) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= maximum
        and all(_short_string(item, 256) for item in value)
    )


def validate_request(
    action: str,
    resource: str,
    params: object,
    *,
    max_attachment_bytes: int = MAX_NATIVE_ATTACHMENT_BYTES,
) -> dict:
    if action not in ACTIONS:
        raise ContractError("corporate_action_invalid")
    if not isinstance(resource, str) or _ISSUE.fullmatch(resource) is None:
        raise ContractError("corporate_resource_invalid")
    if not isinstance(params, dict):
        raise ContractError("corporate_params_invalid")
    normalized = dict(params)
    if action == "comment_create":
        if set(params) - {"text", "attachment_ids"} or not _short_string(
            params.get("text"), 64 * 1024
        ):
            raise ContractError("corporate_params_invalid")
        if "attachment_ids" in params and (
            not isinstance(params["attachment_ids"], list)
            or len(params["attachment_ids"]) > 8
            or any(not _short_string(item, 128) for item in params["attachment_ids"])
        ):
            raise ContractError("corporate_params_invalid")
    elif action == "issue_patch":
        if set(params) - {"fields", "version"} or not isinstance(params.get("fields"), dict):
            raise ContractError("corporate_params_invalid")
        fields = params["fields"]
        if not fields or set(fields) - _PATCH_FIELDS:
            raise ContractError("corporate_fields_invalid")
        for key, value in fields.items():
            if key == "assignee":
                if value is not None and not _short_string(value, 128):
                    raise ContractError("corporate_fields_invalid")
            elif key in {"tags", "components"}:
                if not _string_list(value):
                    raise ContractError("corporate_fields_invalid")
            elif not _short_string(value, 1024):
                raise ContractError("corporate_fields_invalid")
        if "version" in params and (
            not isinstance(params["version"], str)
            or re.fullmatch(r"[0-9]{1,20}", params["version"]) is None
        ):
            raise ContractError("corporate_params_invalid")
    elif action == "transition_execute":
        if (
            set(params) - {"transition", "resolution"}
            or not isinstance(params.get("transition"), str)
            or _TRANSITION.fullmatch(params["transition"]) is None
        ):
            raise ContractError("corporate_params_invalid")
        if "resolution" in params and not _short_string(params["resolution"], 128):
            raise ContractError("corporate_params_invalid")
    else:
        if set(params) != {"filename", "content_type", "data_base64"}:
            raise ContractError("corporate_params_invalid")
        filename = params["filename"]
        if (
            not _short_string(filename, 255)
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or "\r" in filename
            or "\n" in filename
            or "\x00" in filename
            or params["content_type"] not in _CONTENT_TYPES
            or not isinstance(params["data_base64"], str)
        ):
            raise ContractError("corporate_params_invalid")
        try:
            decoded = base64.b64decode(params["data_base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ContractError("corporate_params_invalid") from exc
        if not decoded or len(decoded) > max_attachment_bytes:
            raise ContractError("corporate_attachment_too_large")
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ContractError("corporate_params_invalid") from exc
    limit = (
        MAX_REQUEST_BYTES
        if action != "attachment_upload"
        else (max_attachment_bytes * 4 // 3 + 4096)
    )
    if len(encoded) > limit:
        raise ContractError("corporate_request_too_large")
    return normalized


def validate_step_id(value: str) -> str:
    if not isinstance(value, str) or _STEP.fullmatch(value) is None:
        raise ContractError("corporate_step_invalid")
    return value


def validate_result(
    value: object,
    *,
    command_id: str,
    step_id: str,
    action: str,
    resource: str,
    owner_user_id: int,
    owner_tracker_login: str,
) -> CorporateWriteResponse:
    validate_result_shape(value)
    if not isinstance(value, dict) or set(value) != {
        "service",
        "action",
        "resource",
        "owner_user_id",
        "identity_login",
        "command_id",
        "step_id",
        "status_code",
        "payload",
    }:
        raise ContractError("corporate_response_invalid")
    if (
        value["service"] != "tracker"
        or value["action"] != action
        or value["resource"] != resource
        or value["owner_user_id"] != owner_user_id
        or not isinstance(value["identity_login"], str)
        or value["identity_login"].casefold() != owner_tracker_login.casefold()
        or value["command_id"] != command_id
        or value["step_id"] != step_id
        or type(value["status_code"]) is not int
        or not 200 <= value["status_code"] <= 299
        or not isinstance(value["payload"], dict)
    ):
        raise ContractError("corporate_response_invalid")
    payload = value["payload"]
    if action in {"comment_create", "attachment_upload"}:
        if set(payload) != {"id"} or not _short_string(payload.get("id"), 256):
            raise ContractError("corporate_response_invalid")
    elif payload:
        raise ContractError("corporate_response_invalid")
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
        raise ContractError("corporate_response_invalid")
    return CorporateWriteResponse(value["status_code"], payload)


def validate_result_shape(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "service",
        "action",
        "resource",
        "owner_user_id",
        "identity_login",
        "command_id",
        "step_id",
        "status_code",
        "payload",
    }:
        raise ContractError("corporate_response_invalid")
    if (
        value["service"] != "tracker"
        or value["action"] not in ACTIONS
        or not isinstance(value["resource"], str)
        or _ISSUE.fullmatch(value["resource"]) is None
        or type(value["owner_user_id"]) is not int
        or value["owner_user_id"] <= 0
        or not _short_string(value["identity_login"], 128)
        or not isinstance(value["command_id"], str)
        or not _short_string(value["step_id"], 64)
        or _STEP.fullmatch(value["step_id"]) is None
        or type(value["status_code"]) is not int
        or not 200 <= value["status_code"] <= 299
        or not isinstance(value["payload"], dict)
    ):
        raise ContractError("corporate_response_invalid")
    if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
        raise ContractError("corporate_response_invalid")
    return value
