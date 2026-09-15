"""Lease-backed delivery of durable local actions to Tracker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import User
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import tracker_cache, tracker_client, tracker_signatures
from robopark_api.services.reliable_actions import (
    claim_due_batch,
    complete_action,
    mark_needs_attention,
    schedule_retry,
)
from robopark_api.services.task_timeline import staged_attachments_root
from robopark_api.services.tracker_transitions import (
    TransitionPurpose,
    resolve_transition,
    target_status_reached,
)
from robopark_api.task_workflow_models import ReliableAction, TaskAttachment, TaskMessage

logger = logging.getLogger(__name__)

_TRANSITION_ACTIONS = frozenset({"start", "review", "return", "close"})
_ALLOWED_TRACKER_FIELDS = {
    "theDefectCode": "60df26695151a36df681d67b--theDefectCode",
}


class DeliveryError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _signed_text(db: Session, actor: User, issue: dict, body: str) -> str:
    context = tracker_signatures.build_signature_context(db, actor, issue)
    return tracker_signatures.format_signed_comment(
        body=body,
        park_name=context.park_name,
        mechanic_login=context.mechanic_login,
        operator_login=context.operator_login,
        actor_login=context.actor_login,
    )


def _external_id(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("id") or result.get("longId") or "")
    return ""


def _set_issue_field(*, token: str, key: str, field_id: str, value: object) -> None:
    client = tracker_client._client(token)  # noqa: SLF001 - SDK write facade is private today.

    def update_field() -> None:
        client.issues[key].update(**{field_id: value})

    tracker_client._run_mutation(update_field)  # noqa: SLF001


def _attachment_temp_id(action: ReliableAction) -> str | None:
    if not action.result_json:
        return None
    try:
        value = json.loads(action.result_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    temp_id = str(value.get("temp_attachment_id") or "").strip()
    return temp_id or None


def _deliver_attachment(
    db: Session,
    action: ReliableAction,
    *,
    token: str,
    issue: dict,
    actor: User,
) -> dict[str, str]:
    attachment = db.get(TaskAttachment, action.id)
    if attachment is None:
        raise DeliveryError("invalid_payload")
    message = db.get(TaskMessage, attachment.message_id)
    if message is None or message.issue_key != action.resource_id:
        raise DeliveryError("invalid_payload")

    temp_id = _attachment_temp_id(action)
    if temp_id is None:
        path = staged_attachments_root() / attachment.blob_name
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise DeliveryError("invalid_payload") from exc
        temp_id = tracker_client.upload_temp_attachment(
            token=token,
            filename=attachment.original_name,
            content=content,
            content_type=attachment.mime_type,
        )
        attachment.uploaded_at = time.time()
        action.result_json = json.dumps({"temp_attachment_id": temp_id}, separators=(",", ":"))
        db.commit()

    result = tracker_client.add_comment(
        token=token,
        key=action.resource_id,
        text=_signed_text(db, actor, issue, message.text),
        attachment_ids=[temp_id],
    )
    return {"external_id": _external_id(result), "attachment_id": temp_id}


def _deliver_transition(
    action: ReliableAction,
    *,
    token: str,
    issue: dict,
) -> dict[str, str | bool]:
    purpose: TransitionPurpose = action.action  # type: ignore[assignment]
    if target_status_reached(issue, purpose):
        return {"already_applied": True}
    transitions = tracker_client.list_transitions(token=token, key=action.resource_id)
    transition_id = resolve_transition(transitions, purpose)
    if transition_id is None:
        raise DeliveryError("tracker_transition_missing")
    payload = json.loads(action.payload_json)
    tracker_client.transition_issue(
        token=token,
        key=action.resource_id,
        transition=transition_id,
        resolution=payload.get("resolution"),
    )
    return {"transition": transition_id}


def _deliver_action(db: Session, action: ReliableAction) -> dict[str, Any]:
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise DeliveryError("authentication")
    actor = db.get(User, action.actor_user_id)
    if actor is None:
        raise DeliveryError("invalid_payload")
    try:
        payload = json.loads(action.payload_json)
    except (TypeError, ValueError) as exc:
        raise DeliveryError("invalid_payload") from exc
    if not isinstance(payload, dict):
        raise DeliveryError("invalid_payload")

    # This intentionally bypasses tracker_cache: replay safety needs fresh state.
    issue = tracker_client.get_issue(token=token, key=action.resource_id)
    if issue is None:
        raise DeliveryError("invalid_payload")

    if action.action == "comment":
        body = str(payload.get("text") or "").strip()
        if not body:
            raise DeliveryError("invalid_payload")
        result = tracker_client.add_comment(
            token=token,
            key=action.resource_id,
            text=_signed_text(db, actor, issue, body),
        )
        return {"external_id": _external_id(result)}
    if action.action == "attach":
        return _deliver_attachment(db, action, token=token, issue=issue, actor=actor)
    if action.action in _TRANSITION_ACTIONS:
        return _deliver_transition(action, token=token, issue=issue)
    if action.action == "set_field":
        field = str(payload.get("field") or "")
        field_id = _ALLOWED_TRACKER_FIELDS.get(field)
        if field_id is None or "value" not in payload:
            raise DeliveryError("invalid_payload")
        _set_issue_field(
            token=token,
            key=action.resource_id,
            field_id=field_id,
            value=payload["value"],
        )
        return {"field": field}
    raise DeliveryError("invalid_payload")


def _tracker_error_code(exc: tracker_client.TrackerError) -> str:
    text = str(exc).casefold()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "network" in text or "connection" in text:
        return "network"
    for status_code in re.findall(r"\b[1-5]\d\d\b", text):
        return status_code
    if "auth" in text or "unauthorized" in text:
        return "authentication"
    if "forbidden" in text:
        return "forbidden"
    return "tracker_error"


def _sync_message(db: Session, action: ReliableAction) -> None:
    message = db.scalar(select(TaskMessage).where(TaskMessage.action_id == action.id))
    if message is None and action.action == "attach":
        attachment = db.get(TaskAttachment, action.id)
        if attachment is not None:
            message = db.get(TaskMessage, attachment.message_id)
    if message is None:
        return
    if action.state == "succeeded":
        message.sync_state = "synced"
    elif action.state == "needs_attention":
        message.sync_state = "needs_attention"
    else:
        message.sync_state = "pending"
    message.updated_at = action.updated_at


def _process_batch(session_factory) -> int:
    with session_factory() as db:
        actions = claim_due_batch(db)
        for action in actions:
            try:
                result = _deliver_action(db, action)
                complete_action(db, action, result)
                _sync_message(db, action)
                db.commit()
                tracker_cache.invalidate_issue(action.resource_id)
            except DeliveryError as exc:
                mark_needs_attention(db, action, error_code=exc.code)
                _sync_message(db, action)
                db.commit()
            except tracker_client.TrackerError as exc:
                schedule_retry(db, action, error_code=_tracker_error_code(exc))
                _sync_message(db, action)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
                logger.exception("Unexpected Tracker outbox delivery failure for %s", action.id)
        return len(actions)


async def run_tracker_outbox_loop(
    session_factory,
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 1.0,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_process_batch, session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Tracker outbox cycle failed")
        if stop_event.is_set():
            break
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
