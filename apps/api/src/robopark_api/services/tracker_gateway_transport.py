"""Route the existing Tracker SDK through typed, owner-bound native reads.

GatewayCredential is a request context, never an OAuth token. No HTTP endpoint
accepts it from a client. A separately configured server credential is the
primary fixed-endpoint reader; an eligible owner Mac is an availability-only
fallback and never receives that credential.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from fastapi import HTTPException

from robopark_api.config import get_settings


class GatewayCredential(str):
    requester_user_id: int
    server_token: str | None

    def __new__(cls, requester_user_id: int, *, server_token: str | None = None):
        if type(requester_user_id) is not int or requester_user_id <= 0:
            raise ValueError("invalid_gateway_requester")
        value = super().__new__(cls, f"mac-gateway-v1:{requester_user_id}")
        value.requester_user_id = requester_user_id
        # This credential stays on the API server and is never serialized into
        # a Mac job, a cache key, the context's string value, or a response.
        value.server_token = server_token or None
        return value


def enabled() -> bool:
    settings = get_settings()
    return bool(
        settings.robopark_environment == "test"
        and settings.mac_corporate_gateway_enabled
        and settings.emergency_bridge_enabled
    )


def credential(db, user):
    from robopark_api.services import gateway_policy, platform_settings

    if enabled():
        if not gateway_policy.requester_can_gateway(db, user, "tracker"):
            raise HTTPException(403, "corporate_access_denied")
        fallback = (
            platform_settings.get_tracker_token(db)
            if get_settings().mac_corporate_server_fallback_enabled
            else None
        )
        if not gateway_policy.eligible_gateway_owner_ids(db, user, "tracker"):
            if fallback:
                return fallback
            raise HTTPException(503, "corporate_gateway_owner_unavailable")
        return GatewayCredential(user.id, server_token=fallback)
    return platform_settings.get_tracker_token(db)


class GatewayWriteCredential(GatewayCredential):
    command_id: str
    resource: str

    def __new__(cls, requester_user_id, *, command_id, resource, server_token=None):
        value = super().__new__(cls, requester_user_id, server_token=server_token)
        value.command_id = command_id
        value.resource = resource
        return value


def write_credential(
    db, user, *, resource, command_id=None, force_server=False, action="comment_create"
):
    """Pin one authorized command before its first external mutation.

    Callers must authorize the current issue/action before this boundary. Bot
    integrations continue using their plain service token and never enter it.
    """
    from robopark_api.services import platform_settings

    if not enabled():
        return platform_settings.get_tracker_token(db)
    from robopark_api.services import corporate_write_client

    server_token = (
        platform_settings.get_tracker_token(db)
        if get_settings().mac_corporate_server_fallback_enabled
        else None
    )
    command_id = command_id or str(uuid4())
    try:
        corporate_write_client.prepare(
            db,
            requester_user_id=user.id,
            command_id=command_id,
            resource=resource,
            action=action,
            allow_server=bool(server_token),
            force_server=force_server,
        )
    except corporate_write_client.CorporateWriteError as exc:
        raise _write_error(exc) from exc
    return GatewayWriteCredential(
        user.id, command_id=command_id, resource=resource, server_token=server_token
    )


def write(context, *, action, resource, params):
    from robopark_api.services import tracker_client

    if not isinstance(context, GatewayWriteCredential) or resource != context.resource:
        raise tracker_client.TrackerError("corporate_write_context_required")
    from robopark_api.services import corporate_write_client

    try:
        return corporate_write_client.execute(
            command_id=context.command_id,
            requester_user_id=context.requester_user_id,
            action=action,
            resource=resource,
            params=params,
            server_token=context.server_token,
        ).payload
    except corporate_write_client.CorporateWriteError as exc:
        raise _write_error(exc) from exc


def _write_error(exc):
    from robopark_api.services.tracker_client import TrackerError

    error = TrackerError(exc.code)
    error.gateway_code = exc.code
    error.status_code = exc.status_code
    return error


_ISSUE = r"[A-Z][A-Z0-9_]{0,63}-[1-9][0-9]{0,11}"
_QUEUE = r"[A-Z][A-Z0-9_]{0,63}"
_USER = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
_PATHS = (
    ("GET", rf"/v[23]/issues/({_ISSUE})", "issue_read"),
    ("GET", rf"/v[23]/issues/({_ISSUE})/comments", "comments_read"),
    ("GET", rf"/v[23]/issues/({_ISSUE})/transitions", "transitions_read"),
    ("GET", rf"/v[23]/issues/({_ISSUE})/changelog", "history_read"),
    ("GET", rf"/v[23]/queues/({_QUEUE})", "queue_read"),
    ("GET", rf"/v[23]/queues/({_QUEUE})/components", "queue_components_read"),
    ("GET", rf"/v[23]/users/({_USER})", "user_read"),
)


def typed_request(method, url, *, params=None, data=None, files=None, stream=False):
    """Translate SDK requests into a closed operation registry, never a proxy URL."""
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "st-api.yandex-team.ru"
        or parts.port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or files
        or stream
    ):
        raise ValueError("unsupported_gateway_request")
    parameters = {}
    seen = set()
    pairs = list(parse_qsl(parts.query, keep_blank_values=True))
    if params:
        pairs += list(params.items()) if isinstance(params, dict) else list(params)
    for name, value in pairs:
        if value is None:
            continue
        name = "per_page" if name in {"perPage", "per_page"} else name
        name = "stale_ok" if name == "staleOk" else name
        if name in seen:
            raise ValueError("unsupported_gateway_parameter")
        seen.add(name)
        # Generated next links include empty projection placeholders.
        # They request no expansion; nonempty embed remains unsupported.
        if name in {"expand", "embed", "fields"} and value == "":
            continue
        if name not in {"page", "per_page", "expand", "fields", "stale_ok"}:
            raise ValueError("unsupported_gateway_parameter")
        if name == "stale_ok":
            if method != "POST" or parts.path not in {"/v2/issues/_search", "/v3/issues/_search"}:
                raise ValueError("unsupported_gateway_parameter")
            if value in ("true", "false"):
                value = value == "true"
            if type(value) is not bool:
                raise ValueError("unsupported_gateway_parameter")
        if (
            name in {"page", "per_page"}
            and isinstance(value, str)
            and value.isascii()
            and value.isdigit()
        ):
            value = int(value)
        parameters[name] = value
    for verb, path, action in _PATHS:
        match = re.fullmatch(path, parts.path.rstrip("/"))
        if method == verb and match and data is None:
            return action, match[1], parameters
    if method == "POST" and parts.path in {
        "/v2/issues/_search",
        "/v3/issues/_search",
        "/v2/issues/_count",
        "/v3/issues/_count",
    }:
        if not isinstance(data, str) or len(data.encode()) > 8192:
            raise ValueError("invalid_gateway_query")
        body = json.loads(data)
        if not isinstance(body, dict):
            raise ValueError("invalid_gateway_query")
        for name in ("filter", "filterId", "keys", "queue"):
            if body.get(name) is not None:
                raise ValueError("unsupported_gateway_query")
            body.pop(name, None)
        if set(body) - {"query", "order"} or not isinstance(body.get("query"), str):
            raise ValueError("unsupported_gateway_query")
        parameters.update(body)
        if parameters.get("order") is None:
            parameters.pop("order", None)
        if parts.path.endswith("_count"):
            parameters.pop("order", None)
        return (
            ("issues_count", "count", parameters)
            if parts.path.endswith("_count")
            else ("issues_search", "search", parameters)
        )
    raise ValueError("unsupported_gateway_operation")


def connection(context: GatewayCredential):
    from requests import Request, RequestException, Response, Session
    from yandex_tracker_client.connection import Connection
    from yandex_tracker_client.exceptions import TrackerClientError

    from robopark_api.services import corporate_gateway_client

    class NativeSession:
        headers = {}

        def __init__(self):
            self.server = Session() if context.server_token else None
            if self.server is not None:
                self.server.headers.update(
                    {
                        "Authorization": f"OAuth {context.server_token}",
                        "Content-Type": "application/json",
                        "User-Agent": "Robopark corporate server read",
                    }
                )

        def close(self):
            if self.server is not None:
                self.server.close()

        def request(self, *, method, url, params=None, data=None, files=None, stream=False, **_):
            try:
                action, resource, parameters = typed_request(
                    method, url, params=params, data=data, files=files, stream=stream
                )
            except (ValueError, TypeError) as exc:
                raise TrackerClientError("unsupported_gateway_operation") from exc
            response = Response()
            # SDK pagination replays the original method/body from the response.
            # Keep a credential-free request for owner-Mac responses as well.
            response.request = Request(method=method, url=url, params=params, data=data).prepare()
            response.url = url
            response.encoding = "utf-8"
            response.headers["Content-Type"] = "application/json"
            if self.server is not None:
                try:
                    server_response = self.server.request(
                        method=method,
                        url=url,
                        params=params,
                        data=data,
                        timeout=10,
                        allow_redirects=False,
                    )
                except RequestException:
                    server_response = None
                if server_response is not None and server_response.status_code < 500:
                    return server_response
            try:
                result = corporate_gateway_client.read(
                    service="tracker",
                    action=action,
                    resource=resource,
                    params=parameters,
                    requester_user_id=context.requester_user_id,
                    deadline_ms=15000,
                )
                response.status_code = result.status_code
                response.headers.update(result.headers)
                payload = result.payload
            except corporate_gateway_client.CorporateGatewayError as exc:
                response.status_code = exc.status_code
                payload = {"errorMessages": [exc.code]}
            response._content = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            return response

    conn = Connection(base_url="https://st-api.yandex-team.ru", retries=0, timeout=15)
    conn.session.close()
    conn.session = NativeSession()
    return conn
