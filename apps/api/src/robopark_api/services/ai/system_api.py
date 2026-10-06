"""Bounded access to the real local HTTP API through its OpenAPI contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import quote, unquote

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from robopark_api.config import get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import AccessStatus, Park, User, UserPark
from robopark_api.services import emergency_config, rbac
from robopark_api.services.ai import policy

MAX_ARGUMENT_BYTES = 16_384
MAX_RESULT_BYTES = 7_000
MAX_REMOTE_HISTORY_GUARDS = 8
ALLOWED_HEADERS = frozenset({"if-match", "idempotency-key"})
SENSITIVE_KEYS = re.compile(
    r"(?i)(?:.*password.*|.*passphrase.*|.*secret.*|.*credential.*|authorization|auth|cookie|totp|token|(?:access|refresh|auth)[_-]?token|api[_-]?key|p256dh|private[_-]?key|bearer)"
)
SAFE_KEY_NAMES = frozenset({"idempotency_key", "issue_key"})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
BOOL_ADAPTER = TypeAdapter(bool)


class SystemAPIInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["list", "describe", "call"]
    operation_id: str | None = Field(default=None, min_length=1, max_length=240)
    search: str = Field(default="", max_length=120)
    category: Literal["read", "mutation", "interactive"] | None = None
    offset: int = Field(default=0, ge=0, le=10_000)
    limit: int = Field(default=15, ge=1, le=15)
    path: dict[str, str | int] = Field(default_factory=dict, max_length=24)
    query: dict[str, str | int | float | bool | list[str | int | float | bool]] = Field(
        default_factory=dict, max_length=32
    )
    body: dict[str, Any] | list[Any] | None = None
    headers: dict[str, str] = Field(default_factory=dict, max_length=2)

    @model_validator(mode="after")
    def action_shape(self):
        if self.action in {"describe", "call"} and self.operation_id is None:
            raise ValueError("operation_id required")
        if self.action != "list" and (self.search or self.category is not None):
            raise ValueError("filters only belong to list")
        if self.action != "call" and (
            self.path or self.query or self.body is not None or self.headers
        ):
            raise ValueError("request data only belongs to call")
        return self


def _json_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(422, "ai_tool_arguments_invalid") from None


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_sensitive_key(key) or _contains_secret(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def _sensitive_key(value: Any) -> bool:
    key = str(value).casefold()
    return key not in SAFE_KEY_NAMES and SENSITIVE_KEYS.fullmatch(key) is not None


def parse(arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if not isinstance(arguments, dict):
            raise TypeError
        value = SystemAPIInput.model_validate(arguments).model_dump(mode="json")
    except (TypeError, ValidationError):
        raise HTTPException(422, "ai_tool_arguments_invalid") from None
    headers = {key.casefold(): item for key, item in value["headers"].items()}
    if set(headers) - ALLOWED_HEADERS:
        raise HTTPException(422, "ai_system_api_header_forbidden")
    value["headers"] = headers
    if any(_contains_secret(value.get(key)) for key in ("path", "query", "body")):
        raise HTTPException(422, "ai_system_api_secret_forbidden")
    if _json_size(value) > MAX_ARGUMENT_BYTES:
        raise HTTPException(422, "ai_tool_arguments_invalid")
    return value


def schema() -> dict[str, Any]:
    # Keep the broker-facing schema shallow. Full per-operation schemas are
    # available through describe and never inflate every model turn.
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["list", "describe", "call"]},
            "operation_id": {"type": "string", "maxLength": 240},
            "search": {"type": "string", "maxLength": 120},
            "category": {
                "type": "string",
                "enum": ["read", "mutation", "interactive"],
            },
            "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
            "limit": {"type": "integer", "minimum": 1, "maximum": 15},
            "path": {"type": "object", "maxProperties": 24},
            "query": {"type": "object", "maxProperties": 32},
            "body": {},
            "headers": {"type": "object", "maxProperties": 2},
        },
    }


def _resolve_ref(value: Any, schemas: dict[str, Any], *, depth: int = 0) -> Any:
    """Resolve local schema refs without returning the whole OpenAPI components tree."""
    if depth >= 8:
        return {"truncated": True}
    if isinstance(value, list):
        return [_resolve_ref(item, schemas, depth=depth + 1) for item in value[:50]]
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        name = reference.rsplit("/", 1)[-1]
        target = schemas.get(name)
        if isinstance(target, dict):
            return {
                "schema": name,
                **_resolve_ref(target, schemas, depth=depth + 1),
            }
    allowed = {
        "type",
        "name",
        "in",
        "format",
        "title",
        "description",
        "default",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "required",
        "properties",
        "items",
        "additionalProperties",
        "anyOf",
        "oneOf",
        "allOf",
        "nullable",
        "content",
        "schema",
        "responses",
    }
    result = {}
    for key, item in value.items():
        if key in {"properties", "content", "responses"} and isinstance(item, dict):
            result[key] = {
                str(name): _resolve_ref(child, schemas, depth=depth + 1)
                for name, child in list(item.items())[:100]
            }
        elif key in allowed:
            result[key] = _resolve_ref(item, schemas, depth=depth + 1)
    return result


def _schema_has_secret(
    value: Any, schemas: dict[str, Any], *, seen: frozenset[str] = frozenset()
) -> bool:
    if isinstance(value, list):
        return any(_schema_has_secret(item, schemas, seen=seen) for item in value)
    if not isinstance(value, dict):
        return False
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
        name = reference.rsplit("/", 1)[-1]
        if name in seen:
            return False
        return _schema_has_secret(schemas.get(name), schemas, seen=seen | {name})
    properties = value.get("properties", {})
    if isinstance(properties, dict) and any(_sensitive_key(key) for key in properties):
        return True
    return any(_schema_has_secret(item, schemas, seen=seen) for item in value.values())


def _interactive_reason(
    path: str, method: str, operation: dict[str, Any], schemas: dict[str, Any]
) -> str | None:
    lowered = path.casefold()
    if lowered == "/ai" or lowered.startswith("/ai/"):
        return "recursive_ai"
    if lowered == "/auth" or lowered.startswith("/auth/") or "privileged-auth" in lowered:
        return "interactive_auth"
    if lowered.startswith("/internal/bot"):
        return "internal_bot"
    if lowered == "/push" or lowered.startswith("/push/"):
        return "browser_push_credentials"
    if any(
        part in lowered
        for part in (
            "/terminal",
            "/admin/ops",
            "/admin/ota",
            "/admin/system",
            "/admin/health",
        )
    ):
        return "host_or_terminal"
    if lowered.startswith("/admin/settings"):
        return "credentials_or_runtime_settings"
    if any(
        word in lowered for word in ("token", "password", "cookie", "credential", "totp", "secret")
    ):
        return "credentials"
    if any(
        _sensitive_key(parameter.get("name", "")) for parameter in operation.get("parameters", [])
    ):
        return "credentials"
    if path in {
        "/inventory/export",
        "/inventory/components/{component_id}/photo",
        "/inventory/parts/{part_id}/photo",
        "/reports/{report_id}/attachments/{attachment_id}",
        "/tracker/issues/{key}/attachments/{attachment_id}/content",
    }:
        return "binary_or_non_json_response"
    request_content = set(operation.get("requestBody", {}).get("content", {}))
    if request_content and request_content != {"application/json"}:
        return "upload_or_non_json_request"
    if _schema_has_secret(operation.get("requestBody"), schemas):
        return "credentials"
    if method == "GET":
        success_content = {
            content_type
            for status, response in operation.get("responses", {}).items()
            if str(status).startswith("2")
            for content_type in response.get("content", {})
        }
        if success_content and not success_content.issubset({"application/json"}):
            return "binary_or_non_json_response"
        if not _history_supported_path(path):
            return "history_scope_unsupported"
    return None


def _history_supported_path(path: str) -> bool:
    exact = {
        "/changes",
        "/health",
        "/health/ready",
        "/maintenance/status",
        "/mechanic/tasks",
        "/ops/maintenance",
        "/parks",
        "/reports/badge",
        "/tracker/defect-codes",
        "/tracker/users",
    }
    prefixes = (
        "/admin/audit",
        "/admin/bot",
        "/admin/diagnostic-rules",
        "/admin/diagnostic-unknowns",
        "/admin/emergency-readings",
        "/admin/emergency/",
        "/admin/park-requests",
        "/admin/roles",
        "/admin/users",
        "/analytics",
        "/campaigns",
        "/dashboard/",
        "/emergency/",
        "/inventory",
        "/mechanic/emergency/",
        "/reports/",
        "/robots",
        "/schedules",
        "/sdc-inventory/",
        "/tracker/issues/{key}",
        "/tracker/transitions/{key}",
    )
    return path in exact or path.startswith(prefixes)


def _parameters(operation: dict[str, Any], location: str) -> list[dict[str, Any]]:
    return [item for item in operation.get("parameters", []) if item.get("in") == location]


@lru_cache(maxsize=1)
def _openapi() -> dict[str, Any]:
    # Imported lazily to avoid a tool-domain/main import cycle in worker startup.
    from robopark_api.main import create_app

    return create_app().openapi()


@lru_cache(maxsize=1)
def _registry() -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    openapi = _openapi()
    schemas = openapi.get("components", {}).get("schemas", {})
    for path, path_item in openapi.get("paths", {}).items():
        for raw_method, operation in path_item.items():
            method = raw_method.upper()
            if method not in METHODS or not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or "")
            counts[operation_id] = counts.get(operation_id, 0) + 1
            reason = _interactive_reason(path, method, operation, schemas)
            entries.append(
                {
                    "operation_id": operation_id,
                    "method": method,
                    "path": path,
                    "summary": str(operation.get("summary") or ""),
                    "classification": "interactive"
                    if reason
                    else "read"
                    if method == "GET"
                    else "mutation",
                    "reason": reason,
                    "confirmation_required": method in {"POST", "DELETE"}
                    or bool(
                        re.search(r"(?i)(close|delete|reverse|merge)", operation_id + " " + path)
                    ),
                    "path_parameters": tuple(
                        item["name"] for item in _parameters(operation, "path")
                    ),
                    "query_parameters": tuple(
                        item["name"] for item in _parameters(operation, "query")
                    ),
                    "header_parameters": tuple(
                        item["name"].casefold() for item in _parameters(operation, "header")
                    ),
                    "operation": operation,
                }
            )
    for entry in entries:
        if not entry["operation_id"] or counts[entry["operation_id"]] != 1:
            entry["classification"] = "interactive"
            entry["reason"] = "duplicate_or_missing_operation_id"
    entries.sort(key=lambda item: (item["path"], item["method"], item["operation_id"]))
    return tuple(entries)


def registry() -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in entry.items()
            if key not in {"operation", "path_parameters", "query_parameters", "header_parameters"}
        }
        for entry in _registry()
    ]


def _find(operation_id: str) -> dict[str, Any]:
    matches = [entry for entry in _registry() if entry["operation_id"] == operation_id]
    if len(matches) != 1:
        raise HTTPException(404, "ai_system_api_operation_not_found")
    return matches[0]


def is_readonly(arguments: dict[str, Any]) -> bool:
    action = arguments.get("action")
    return action in {"list", "describe"} or (
        action == "call" and _find(str(arguments.get("operation_id")))["classification"] == "read"
    )


def _entry_view(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry[key]
        for key in (
            "operation_id",
            "method",
            "path",
            "summary",
            "classification",
            "reason",
            "confirmation_required",
        )
    }


def _signature(entry: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            [entry["operation_id"], entry["method"], entry["path"], entry["classification"]],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _actor_allowed(user: User) -> None:
    if (
        not user.is_active
        or user.must_change_password
        or user.access_status != AccessStatus.approved.value
    ):
        raise HTTPException(403, "ai_system_api_forbidden")


def _scope(db, user: User) -> dict[str, Any]:
    return {
        "role": rbac.role_slug(user),
        "permissions": sorted(rbac.permissions_for_user(db, user)),
        "park_ids": sorted(policy.parks(db, user)),
        "assigned_park_ids": sorted(
            db.scalars(select(UserPark.park_id).where(UserPark.user_id == user.id))
        ),
    }


def _visible_park_ids(db, user: User) -> list[int]:
    fleet_permissions = {
        rbac.PERMISSION_NAV_ADMIN,
        rbac.PERMISSION_USERS_MANAGE,
        rbac.PERMISSION_PARKS_MANAGE,
    }
    if rbac.permissions_for_user(db, user) & fleet_permissions:
        return sorted(db.scalars(select(Park.id)))
    return sorted(policy.parks(db, user))


def _history_guard(db, user: User, entry: dict[str, Any], args: dict[str, Any], park_id):
    path = entry["path"]
    if path.startswith("/sdc-inventory/robots/"):
        requested_park = args["query"].get("park_id", park_id)
        try:
            requested_park = int(requested_park) if requested_park is not None else None
        except (TypeError, ValueError):
            return None
        return {
            "v": 1,
            "family": "emergency_robot",
            "robot": str(args["path"]["robot"]),
            "park_id": requested_park,
        }
    if path.startswith("/emergency/") or path.startswith("/mechanic/emergency/"):
        guard = {
            "v": 1,
            "family": "emergency_robot",
            "robot": str(args["path"]["vin"]),
            "park_id": None,
        }
        section = args["path"].get("section_id") or args["query"].get("section")
        if section is not None:
            guard["section"] = str(section)
        if path.endswith("/snapshot") or path.endswith("/view"):
            sections = [
                section_id
                for section_id, _title in emergency_config.list_sections_for_role(
                    db, rbac.role_slug(user)
                )
            ]
            if len(sections) > 64:
                return None
            guard["sections"] = sections
        return guard
    if path.startswith("/tracker/issues/{key}/") or path in {
        "/tracker/issues/{key}",
        "/tracker/transitions/{key}",
    }:
        guard = {
            "v": 1,
            "family": "tracker_issue",
            "key": str(args["path"]["key"]),
        }
        if path == "/tracker/issues/{key}":
            try:
                guard["include_hidden"] = BOOL_ADAPTER.validate_python(
                    args["query"].get("include_hidden", False)
                )
            except ValidationError:
                return None
        if rbac.role_slug(user) == rbac.RoleSlug.MECHANIC and path in {
            "/tracker/issues/{key}/comments",
            "/tracker/issues/{key}/timeline",
        }:
            guard["staff_visibility"] = _staff_visibility(db)
        return guard
    if path == "/parks":
        return {"v": 1, "family": "park_set", "park_ids": _visible_park_ids(db, user)}
    if path in {
        "/inventory/parks/{park_id}/counts",
        "/inventory/parks/{park_id}/receipts",
    }:
        request_park = args["path"].get("park_id", args["query"].get("park_id"))
        try:
            return {"v": 1, "family": "inventory_park", "park_id": int(request_park)}
        except (TypeError, ValueError):
            return None
    if path in {"/analytics", "/analytics/task-keys"}:
        request_park = args["query"].get("park_id")
        try:
            return {"v": 1, "family": "park", "park_id": int(request_park)}
        except (TypeError, ValueError):
            return None
    if path.startswith(
        (
            "/admin/diagnostic-rules",
            "/admin/diagnostic-unknowns",
            "/admin/emergency-readings",
            "/admin/emergency/",
        )
    ):
        return {"v": 1, "family": "admin_or_royal"}
    if _history_supported_path(path):
        return {"v": 1, "family": "router_replay"}
    return None


def _staff_visibility(db) -> str:
    from robopark_api.services import tracker_signatures

    return hashlib.sha256(
        "\x00".join(sorted(tracker_signatures.staff_tracker_logins(db))).encode()
    ).hexdigest()


def prepare(
    db, user: User, arguments: dict[str, Any], *, park_id: int | None = None
) -> dict[str, Any]:
    args = parse(arguments)
    _actor_allowed(user)
    expected: dict[str, Any] = {}
    confirmation_required = False
    if args["action"] in {"describe", "call"}:
        entry = _find(args["operation_id"])
        expected["operation"] = _signature(entry)
        if args["action"] == "call":
            if entry["classification"] == "interactive":
                raise HTTPException(409, "ai_system_api_interactive")
            if entry["classification"] == "mutation":
                confirmation_required = entry["confirmation_required"]
            expected["scope"] = _scope(db, user)
            if entry["classification"] == "read":
                expected["history_guard"] = _history_guard(db, user, entry, args, park_id)
            _validate_request_shape(entry, args)
    preview = _preview(args)
    return {
        "arguments": args,
        "preview": preview,
        "confirmation_required": confirmation_required,
        "expected": expected,
    }


def _validate_request_shape(entry: dict[str, Any], args: dict[str, Any]) -> None:
    path_names = set(entry["path_parameters"])
    if set(args["path"]) != path_names:
        raise HTTPException(422, "ai_system_api_path_invalid")
    if set(args["query"]) - set(entry["query_parameters"]):
        raise HTTPException(422, "ai_system_api_query_forbidden")
    if set(args["headers"]) - set(entry["header_parameters"]):
        raise HTTPException(422, "ai_system_api_header_forbidden")
    for raw in args["path"].values():
        decoded = str(raw)
        for _ in range(3):
            decoded = unquote(decoded)
        if decoded in {".", ".."} or any(character in decoded for character in ("/", "\\", "\x00")):
            raise HTTPException(422, "ai_system_api_path_invalid")
    request_content = entry["operation"].get("requestBody", {}).get("content", {})
    if args["body"] is not None and "application/json" not in request_content:
        raise HTTPException(422, "ai_system_api_body_forbidden")


def _preview(args: dict[str, Any]) -> str:
    if args["action"] == "list":
        return f"Показать операции API с позиции {args['offset']}"
    entry = _find(args["operation_id"])
    if args["action"] == "describe":
        return f"Показать контракт {entry['method']} {entry['path']}"
    return f"Вызвать {entry['method']} {entry['path']}"


def _settings_for(db):
    return getattr(db.get_bind(), "_robopark_ops_settings", None) or get_settings()


def safe_read_body(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[скрыто]" if _sensitive_key(key) else safe_read_body(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe_read_body(item) for item in value]
    if isinstance(value, str):
        from robopark_api.services.ai import knowledge

        return knowledge.redact(value)
    return value


def response_value(
    method: str, status_code: int, body: Any, headers: dict[str, str]
) -> dict[str, Any]:
    if method != "GET":
        sync_state = body.get("sync_state") if isinstance(body, dict) else None
        accepted = status_code == 202 or (sync_state is not None and sync_state != "synced")
        value = {
            "status_code": status_code,
            "succeeded": 200 <= status_code < 300 and not accepted,
            "accepted": accepted,
        }
        if sync_state is not None:
            value["sync_state"] = sync_state
        if isinstance(body, dict) and isinstance(body.get("job_id"), (str, int)):
            value["job_id"] = body["job_id"]
        return value
    value = {"status_code": status_code, "body": safe_read_body(body)}
    if "etag" in headers:
        value["headers"] = {"etag": headers["etag"]}
    if _json_size(value) > MAX_RESULT_BYTES:
        value["body"] = _project_result(value["body"])
        value["result_truncated"] = True
    if _json_size(value) > MAX_RESULT_BYTES:
        raise HTTPException(502, "ai_system_api_result_too_large")
    return value


def _project_result(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return None
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, list):
        return [_project_result(item, depth=depth + 1) for item in value[:8]]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _project_result(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    return value


def _request_path(entry: dict[str, Any], path_values: dict[str, Any]) -> str:
    path = entry["path"]
    for name in entry["path_parameters"]:
        path = path.replace("{" + name + "}", quote(str(path_values[name]), safe=""))
    return path


async def _request(db, settings, user: User, entry: dict[str, Any], args: dict[str, Any]):
    from robopark_api.main import create_app

    bind = db.get_bind()
    factory = sessionmaker(bind=bind, future=True)
    actor_id = user.id
    app = create_app()

    def database():
        with factory() as request_db:
            yield request_db

    def actor(request_db: Session = Depends(get_db)):
        current = request_db.get(User, actor_id, populate_existing=True)
        if current is None:
            raise HTTPException(401)
        _actor_allowed(current)
        return current

    app.dependency_overrides[get_db] = database
    from robopark_api.config import get_settings as settings_dependency

    app.dependency_overrides[settings_dependency] = lambda: settings
    app.dependency_overrides[require_user] = actor
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://system-api.local") as client:
        return await client.request(
            entry["method"],
            _request_path(entry, args["path"]),
            params=args["query"],
            json=args["body"] if args["body"] is not None else None,
            headers=args["headers"],
        )


def _call(
    db,
    settings,
    user: User,
    entry: dict[str, Any],
    args: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    request_args = {**args, "headers": dict(args["headers"])}
    if (
        idempotency_key
        and entry["classification"] == "mutation"
        and "idempotency-key" in entry["header_parameters"]
        and "idempotency-key" not in request_args["headers"]
    ):
        request_args["headers"]["idempotency-key"] = idempotency_key
    response = asyncio.run(_request(db, settings, user, entry, request_args))
    try:
        body = response.json() if response.content else None
    except ValueError:
        raise HTTPException(502, "ai_system_api_non_json_response") from None
    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) and "detail" in body else body
        raise HTTPException(response.status_code, detail)
    return response_value(entry["method"], response.status_code, body, dict(response.headers))


def execute(
    db,
    settings,
    user: User,
    arguments: dict[str, Any],
    *,
    expected: dict[str, Any],
    idempotency_key: str | None = None,
    park_id: int | None = None,
) -> dict[str, Any]:
    args = parse(arguments)
    _actor_allowed(user)
    if args["action"] == "list":
        entries = registry()
        needle = args["search"].strip().casefold()
        if needle:
            entries = [
                item
                for item in entries
                if needle
                in " ".join(
                    (item["operation_id"], item["method"], item["path"], item["summary"])
                ).casefold()
            ]
        if args["category"] is not None:
            entries = [item for item in entries if item["classification"] == args["category"]]
        offset = args["offset"]
        items = entries[offset : offset + args["limit"]]
        next_offset = offset + len(items)
        return {
            "items": items,
            "total": len(entries),
            "next_offset": next_offset if next_offset < len(entries) else None,
        }
    entry = _find(args["operation_id"])
    if expected.get("operation") != _signature(entry):
        raise HTTPException(409, "ai_action_changed")
    if args["action"] == "describe":
        schemas = _openapi().get("components", {}).get("schemas", {})
        return {
            **_entry_view(entry),
            "parameters": _resolve_ref(entry["operation"].get("parameters", []), schemas),
            "requestBody": _resolve_ref(entry["operation"].get("requestBody"), schemas),
            "responses": _resolve_ref(
                {"responses": entry["operation"].get("responses", {})}, schemas
            ).get("responses", {}),
        }
    prepared = prepare(db, user, args, park_id=park_id)
    if prepared["expected"] != expected:
        raise HTTPException(409, "ai_action_changed")
    return _call(db, settings, user, entry, args, idempotency_key=idempotency_key)


def authorize_view(
    db,
    user: User,
    arguments: dict[str, Any],
    *,
    expected: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    scope_cache: dict[tuple, Any] | None = None,
) -> None:
    args = parse(arguments)
    _actor_allowed(user)
    if args["action"] != "call":
        return
    entry = _find(args["operation_id"])
    if entry["classification"] == "mutation":
        if expected is None or expected.get("scope") != _scope(db, user):
            raise HTTPException(409, "ai_action_changed")
        return
    if expected is None or expected.get("scope") != _scope(db, user):
        raise HTTPException(409, "ai_action_changed")
    if result is None:
        return
    if (
        isinstance(result, dict)
        and isinstance(result.get("status_code"), int)
        and result["status_code"] >= 400
    ):
        return
    guard = expected.get("history_guard") if expected is not None else None
    if guard is None:
        raise HTTPException(409, "ai_system_api_history_unavailable")
    _authorize_history_guard(
        db, user, guard, arguments=args, result=result, scope_cache=scope_cache
    )


def _authorize_history_guard(
    db,
    user: User,
    guard: dict[str, Any],
    *,
    arguments: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    scope_cache=None,
) -> None:
    if guard.get("v") != 1:
        raise HTTPException(409, "ai_system_api_history_unavailable")
    family = guard.get("family")
    cache = scope_cache if scope_cache is not None else {}
    if family == "park_set":
        if not set(guard.get("park_ids", [])).issubset(_visible_park_ids(db, user)):
            raise HTTPException(409, "ai_action_changed")
        return
    if family == "park":
        policy.park(db, user, int(guard["park_id"]))
        return
    if family == "inventory_park":
        from robopark_api.services import inventory_access

        try:
            inventory_access.require_park(db, user, int(guard["park_id"]))
        except PermissionError:
            raise HTTPException(403, "ai_system_api_forbidden") from None
        return
    if family == "admin_or_royal":
        if not rbac.is_admin_or_royal(user):
            raise HTTPException(403, "ai_system_api_forbidden")
        return
    if family == "router_replay":
        if not isinstance(arguments, dict):
            raise HTTPException(409, "ai_system_api_history_unavailable")
        entry = _find(arguments["operation_id"])
        cache_key = (
            "system_api_replay",
            user.id,
            json.dumps(arguments, sort_keys=True, ensure_ascii=False),
        )
        if cache_key not in cache:
            claim_remote_guard(cache)
            cache[cache_key] = _call(db, _settings_for(db), user, entry, arguments)
        if not _identities(result).issubset(_identities(cache[cache_key])):
            raise HTTPException(409, "ai_action_changed")
        return
    if family == "emergency_robot":
        from robopark_api.routers.emergency import authorize_emergency_vin
        from robopark_api.services import emergency_vin

        try:
            robot = emergency_vin.normalize_robot_id(str(guard["robot"]))
        except ValueError:
            raise HTTPException(409, "ai_system_api_history_unavailable") from None
        cache_key = ("emergency_robot", user.id, robot, guard.get("park_id"))
        if cache_key not in cache:
            claim_remote_guard(cache)
            authorize_emergency_vin(db, user, robot, park_id=guard.get("park_id"))
            cache[cache_key] = True
        sections = [*guard.get("sections", [])]
        if guard.get("section") is not None:
            sections.append(guard["section"])
        if any(
            not emergency_config.role_can_view_section(db, rbac.role_slug(user), str(section))
            for section in sections
        ):
            raise HTTPException(403, "ai_system_api_forbidden")
        return
    if family == "tracker_issue":
        from robopark_api.services import tracker_client
        from robopark_api.services.ai import issue_context

        cache_key = ("tracker_issue", user.id, guard["key"], None)
        if cache_key not in cache:
            claim_remote_guard(cache)
            token = issue_context.platform_settings.get_tracker_token(db)
            try:
                issue = tracker_client.get_issue(token=token, key=str(guard["key"]))
            except tracker_client.TrackerError as exc:
                raise HTTPException(502, "tracker_upstream_error") from exc
            if issue is None:
                raise HTTPException(404, "ai_issue_unavailable")
            cache[cache_key] = issue
        issue = cache[cache_key]
        include_hidden = guard.get("include_hidden") is True
        if include_hidden and not rbac.is_admin_or_royal(user):
            raise HTTPException(403, "task_hidden_manager_required")
        if issue_context.task_lifecycle.is_hidden(db, str(guard["key"])) and not include_hidden:
            raise HTTPException(404, "ai_issue_unavailable")
        issue_context.enforce_issue_scope(db, user, issue)
        if guard.get("staff_visibility") is not None and guard[
            "staff_visibility"
        ] != _staff_visibility(db):
            raise HTTPException(409, "ai_action_changed")
        return
    raise HTTPException(409, "ai_system_api_history_unavailable")


def claim_remote_guard(cache: dict[tuple, Any]) -> None:
    key = ("system_api", "remote_history_guard_count")
    used = int(cache.get(key, 0))
    if used >= MAX_REMOTE_HISTORY_GUARDS:
        raise HTTPException(409, "ai_system_api_history_unavailable")
    cache[key] = used + 1


def _identities(value: Any) -> set[tuple[str, str]]:
    identities: set[tuple[str, str]] = set()
    if isinstance(value, list):
        for item in value:
            identities.update(_identities(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).casefold()
            if (
                lowered in {"id", "park_id", "robot", "vin", "key", "issue_key"}
                or lowered.endswith("_id")
            ) and isinstance(item, (str, int)):
                identities.add((lowered, str(item)))
            identities.update(_identities(item))
    return identities
