"""Fixed Tracker HTTP writer used only by an already server-pinned command."""

from __future__ import annotations

import base64

import httpx

from robopark_api.services.corporate_write_contract import CorporateWriteResponse

API_BASE = "https://st-api.yandex-team.ru"


class ServerWriteError(RuntimeError):
    def __init__(self, status_code: int, code: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.outcome_unknown = outcome_unknown


def execute(*, token: str, action: str, resource: str, params: dict) -> CorporateWriteResponse:
    headers = {"Authorization": f"OAuth {token}", "User-Agent": "Robopark corporate writer"}
    try:
        with httpx.Client(timeout=60.0, follow_redirects=False) as client:
            if action == "comment_create":
                body = {"text": params["text"]}
                if params.get("attachment_ids"):
                    body["attachmentIds"] = params["attachment_ids"]
                response = client.post(
                    f"{API_BASE}/v2/issues/{resource}/comments", headers=headers, json=body
                )
            elif action == "issue_patch":
                patch_headers = dict(headers)
                if params.get("version"):
                    patch_headers["If-Match"] = f'"{params["version"]}"'
                response = client.patch(
                    f"{API_BASE}/v2/issues/{resource}",
                    headers=patch_headers,
                    json=params["fields"],
                )
            elif action == "transition_execute":
                body = {"resolution": params["resolution"]} if params.get("resolution") else {}
                response = client.post(
                    f"{API_BASE}/v2/issues/{resource}/transitions/{params['transition']}/_execute",
                    headers=headers,
                    json=body,
                )
            else:
                content = base64.b64decode(params["data_base64"], validate=True)
                response = client.post(
                    f"{API_BASE}/v2/attachments/",
                    headers=headers,
                    files={"file": (params["filename"], content, params["content_type"])},
                )
    except httpx.HTTPError as exc:
        raise ServerWriteError(
            409, "corporate_write_outcome_unknown", outcome_unknown=True
        ) from exc
    if not 200 <= response.status_code <= 299 and response.status_code < 400:
        raise ServerWriteError(409, "corporate_write_outcome_unknown", outcome_unknown=True)
    if response.status_code >= 500:
        raise ServerWriteError(409, "corporate_write_outcome_unknown", outcome_unknown=True)
    if response.status_code >= 400:
        code = {
            401: "corporate_authentication",
            403: "corporate_access_denied",
            404: "corporate_not_found",
            409: "corporate_write_conflict",
            429: "corporate_rate_limited",
        }.get(response.status_code, "corporate_write_rejected")
        raise ServerWriteError(response.status_code, code)
    if action in {"comment_create", "attachment_upload"}:
        try:
            value = response.json()
        except ValueError as exc:
            raise ServerWriteError(
                409, "corporate_write_outcome_unknown", outcome_unknown=True
            ) from exc
        identifier = (
            str(value.get("id") or value.get("longId") or "") if isinstance(value, dict) else ""
        )
        if not identifier:
            raise ServerWriteError(409, "corporate_write_outcome_unknown", outcome_unknown=True)
        payload = {"id": identifier}
    else:
        payload = {}
    return CorporateWriteResponse(response.status_code, payload)
