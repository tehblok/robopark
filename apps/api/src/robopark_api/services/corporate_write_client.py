"""API-side client for durable Mac-first corporate writes."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import SessionLocal
from robopark_api.models import CorporateWriteAuthority, CorporateWriteReceipt, User
from robopark_api.services import (
    corporate_gateway_client,
    corporate_server_write,
    corporate_write_contract,
    corporate_write_service,
    gateway_policy,
    rbac,
    tracker_policy,
)
from robopark_api.services.emergency_bridge_client import BridgeError, _signed_post

CorporateWriteError = corporate_write_service.CorporateWriteError
CorporateWriteResponse = corporate_write_contract.CorporateWriteResponse


def _body(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def prepare(
    db: Session,
    *,
    requester_user_id: int,
    command_id: str,
    resource: str,
    allow_server: bool,
    action: str = "comment_create",
    force_server: bool = False,
    settings: Settings | None = None,
) -> CorporateWriteAuthority:
    if action not in corporate_write_contract.ACTIONS:
        raise CorporateWriteError(422, "corporate_action_invalid")
    existing = db.get(CorporateWriteAuthority, command_id)
    if existing is not None:
        if existing.requester_user_id != requester_user_id or existing.resource != resource:
            raise CorporateWriteError(409, "corporate_write_conflict")
        return existing
    settings = settings or get_settings()
    if not corporate_gateway_client.enabled(settings):
        if not allow_server:
            raise CorporateWriteError(503, "corporate_write_unavailable")
        authority = corporate_write_service.pin_authority(
            db,
            command_id=command_id,
            requester_user_id=requester_user_id,
            resource=resource,
            mode="server",
        )
        db.commit()
        return authority
    body = _body(
        {
            "command_id": command_id,
            "action": action,
            "resource": resource,
            "requester_user_id": requester_user_id,
            "allow_server": allow_server,
            "force_server": force_server,
        }
    )
    try:
        response = _signed_post(
            "/internal/v1/corporate-write/prepare", body, settings=settings, timeout_seconds=5
        )
    except BridgeError as exc:
        # The bridge may have committed a Mac pin before its response was lost.
        # Re-read that durable decision first; only a genuinely unprepared
        # command may choose the configured server before its first mutation.
        db.expire_all()
        existing = db.get(CorporateWriteAuthority, command_id)
        if existing is not None:
            if existing.requester_user_id != requester_user_id or existing.resource != resource:
                raise CorporateWriteError(409, "corporate_write_conflict") from exc
            return existing
        if not allow_server:
            raise CorporateWriteError(503, "corporate_write_unavailable") from exc
        authority = corporate_write_service.pin_authority(
            db,
            command_id=command_id,
            requester_user_id=requester_user_id,
            resource=resource,
            mode="server",
        )
        db.commit()
        return authority
    if response.status_code != 200:
        try:
            code = response.json().get("detail")
        except (ValueError, AttributeError):
            code = None
        raise CorporateWriteError(response.status_code, code or "corporate_write_unavailable")
    db.expire_all()
    authority = db.get(CorporateWriteAuthority, command_id)
    if authority is None:
        raise CorporateWriteError(503, "corporate_write_invalid_response")
    return authority


def _server_authorized(db: Session, authority: CorporateWriteAuthority) -> bool:
    requester = db.get(User, authority.requester_user_id)
    if requester is None or not gateway_policy.requester_can_gateway(db, requester, "tracker"):
        return False
    if "-" not in authority.resource:
        return False
    queue = authority.resource.rsplit("-", 1)[0]
    return rbac.is_admin_or_royal(requester) or queue in tracker_policy.allowed_queues_for_user(
        db, requester
    )


def execute(
    *,
    command_id: str,
    requester_user_id: int,
    action: str,
    resource: str,
    params: dict,
    server_token: str | None = None,
    settings: Settings | None = None,
) -> CorporateWriteResponse:
    settings = settings or get_settings()
    with SessionLocal() as db:
        authority = db.get(CorporateWriteAuthority, command_id)
        if authority is None:
            raise CorporateWriteError(404, "corporate_write_not_prepared")
        if authority.requester_user_id != requester_user_id or authority.resource != resource:
            raise CorporateWriteError(409, "corporate_write_conflict")
        limit = (
            corporate_write_contract.MAX_SERVER_ATTACHMENT_BYTES
            if authority.mode == "server"
            else corporate_write_contract.MAX_NATIVE_ATTACHMENT_BYTES
        )
        try:
            normalized = corporate_write_contract.validate_request(
                action, resource, params, max_attachment_bytes=limit
            )
        except corporate_write_contract.ContractError as exc:
            raise CorporateWriteError(422, str(exc)) from exc
        if authority.mode == "mac":
            body = _body(
                {
                    "command_id": command_id,
                    "action": action,
                    "resource": resource,
                    "params": normalized,
                    "requester_user_id": requester_user_id,
                    "deadline_ms": corporate_write_contract.DEADLINE_MS,
                }
            )
            try:
                response = _signed_post(
                    "/internal/v1/corporate-write/execute",
                    body,
                    settings=settings,
                    timeout_seconds=corporate_write_contract.DEADLINE_MS / 1000 + 1,
                )
            except BridgeError as exc:
                raise CorporateWriteError(409, "corporate_write_outcome_unknown") from exc
            if response.status_code != 200:
                try:
                    code = response.json().get("detail")
                except (ValueError, AttributeError):
                    code = None
                raise CorporateWriteError(
                    response.status_code, code or "corporate_write_outcome_unknown"
                )
            value = response.json()
            if (
                not isinstance(value, dict)
                or type(value.get("status_code")) is not int
                or not isinstance(value.get("payload"), dict)
            ):
                raise CorporateWriteError(409, "corporate_write_outcome_unknown")
            return CorporateWriteResponse(value["status_code"], value["payload"])
        if not server_token:
            raise CorporateWriteError(503, "corporate_write_server_credential_missing")
        if not _server_authorized(db, authority):
            raise CorporateWriteError(403, "corporate_access_denied")
        started = corporate_write_service.begin_step(
            db, authority=authority, action=action, params=normalized
        )
        if not started.execute:
            if started.response is not None:
                if started.error_code:
                    raise CorporateWriteError(started.response.status_code, started.error_code)
                return started.response
            raise CorporateWriteError(409, started.error_code or "corporate_write_outcome_unknown")
        db.commit()
        try:
            result = corporate_server_write.execute(
                token=server_token, action=action, resource=resource, params=normalized
            )
        except corporate_server_write.ServerWriteError as exc:
            receipt = db.get(CorporateWriteReceipt, (command_id, started.receipt.step_id))
            if exc.outcome_unknown:
                corporate_write_service.mark_unknown(db, receipt)
            else:
                corporate_write_service.complete_step(
                    db, receipt, status_code=exc.status_code, payload={}, error_code=exc.code
                )
            db.commit()
            raise CorporateWriteError(exc.status_code, exc.code) from exc
        receipt = db.get(CorporateWriteReceipt, (command_id, started.receipt.step_id))
        corporate_write_service.complete_step(
            db, receipt, status_code=result.status_code, payload=result.payload
        )
        db.commit()
        return result
