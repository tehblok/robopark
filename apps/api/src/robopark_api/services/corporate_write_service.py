"""Durable authority and exactly-once fences for corporate writes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import CorporateWriteAuthority, CorporateWriteReceipt
from robopark_api.services.corporate_write_contract import CorporateWriteResponse


class CorporateWriteError(RuntimeError):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class BeginStep:
    receipt: CorporateWriteReceipt
    execute: bool
    response: CorporateWriteResponse | None = None
    error_code: str | None = None


def _uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise CorporateWriteError(422, "corporate_command_invalid") from exc
    if str(parsed) != value:
        raise CorporateWriteError(422, "corporate_command_invalid")
    return value


def pin_authority(
    db: Session,
    *,
    command_id: str,
    requester_user_id: int,
    resource: str,
    mode: str,
    device_id: str | None = None,
    owner_user_id: int | None = None,
    owner_tracker_login: str | None = None,
) -> CorporateWriteAuthority:
    command_id = _uuid(command_id)
    existing = db.get(CorporateWriteAuthority, command_id)
    if existing is not None:
        if existing.requester_user_id != requester_user_id or existing.resource != resource:
            raise CorporateWriteError(409, "corporate_write_conflict")
        return existing
    if mode not in {"mac", "server"}:
        raise CorporateWriteError(422, "corporate_authority_invalid")
    if mode == "mac":
        if not device_id or not owner_user_id or not owner_tracker_login:
            raise CorporateWriteError(422, "corporate_authority_invalid")
    elif device_id is not None or owner_user_id is not None or owner_tracker_login is not None:
        raise CorporateWriteError(422, "corporate_authority_invalid")
    authority = CorporateWriteAuthority(
        id=command_id,
        requester_user_id=requester_user_id,
        service="tracker",
        resource=resource,
        mode=mode,
        device_id=device_id,
        owner_user_id=owner_user_id,
        owner_tracker_login=owner_tracker_login,
    )
    try:
        with db.begin_nested():
            db.add(authority)
            db.flush()
        return authority
    except IntegrityError:
        existing = db.get(CorporateWriteAuthority, command_id)
        if (
            existing is None
            or existing.requester_user_id != requester_user_id
            or existing.resource != resource
        ):
            raise CorporateWriteError(409, "corporate_write_conflict") from None
        return existing


def payload_sha256(action: str, params: dict) -> str:
    value = json.dumps(
        {"action": action, "params": params},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(value).hexdigest()


def step_id(action: str, params: dict) -> str:
    if action == "issue_patch":
        fields = sorted((params.get("fields") or {}).keys())
        suffix = hashlib.sha256(",".join(fields).encode()).hexdigest()[:12]
        return f"issue_patch:{suffix}"
    return action


def begin_step(
    db: Session, *, authority: CorporateWriteAuthority, action: str, params: dict
) -> BeginStep:
    identity = step_id(action, params)
    digest = payload_sha256(action, params)
    receipt = db.get(CorporateWriteReceipt, (authority.id, identity))
    if receipt is not None:
        if receipt.action != action or receipt.payload_sha256 != digest:
            raise CorporateWriteError(409, "corporate_write_conflict")
        if receipt.state == "completed" and receipt.status_code is not None:
            return BeginStep(
                receipt,
                False,
                response=CorporateWriteResponse(
                    receipt.status_code,
                    json.loads(receipt.result_json or "{}"),
                ),
                error_code=receipt.error_code,
            )
        return BeginStep(receipt, False, error_code="corporate_write_outcome_unknown")
    receipt = CorporateWriteReceipt(
        command_id=authority.id,
        step_id=identity,
        action=action,
        payload_sha256=digest,
        state="executing",
    )
    db.add(receipt)
    db.flush()
    return BeginStep(receipt, True)


def complete_step(
    db: Session,
    receipt: CorporateWriteReceipt,
    *,
    status_code: int,
    payload: dict,
    error_code: str | None = None,
) -> None:
    receipt.state = "completed"
    receipt.status_code = status_code
    receipt.result_json = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    receipt.error_code = error_code
    db.flush()


def mark_unknown(db: Session, receipt: CorporateWriteReceipt) -> None:
    receipt.state = "unknown"
    db.flush()
