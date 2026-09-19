"""Lease-backed delivery of durable local actions to Tracker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import CampaignSubmission, Report, User
from robopark_api.services import audit, tracker_cache, tracker_client, tracker_signatures
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.reliable_actions import (
    claim_due_batch,
    complete_action,
    mark_needs_attention,
    schedule_retry,
)
from robopark_api.services.task_timeline import staged_attachments_root
from robopark_api.services.task_lifecycle import tracker_issue_is_closed
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
_ACTION_MARKER_PREFIX = "surp-action:"


class DeliveryError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _dependency_state(db: Session, action: ReliableAction) -> str:
    try:
        payload = json.loads(action.payload_json)
    except (TypeError, ValueError) as exc:
        raise DeliveryError("invalid_payload") from exc
    dependencies = payload.get("depends_on_actions", []) if isinstance(payload, dict) else []
    dependency_ids = payload.get("depends_on_action_ids", []) if isinstance(payload, dict) else []
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) and item for item in dependencies
    ):
        raise DeliveryError("invalid_payload")
    if not isinstance(dependency_ids, list) or not all(
        isinstance(item, str) and item for item in dependency_ids
    ):
        raise DeliveryError("invalid_payload")
    if not dependencies and not dependency_ids:
        return "ready"
    named_rows = db.scalars(
        select(ReliableAction).where(
            ReliableAction.actor_user_id == action.actor_user_id,
            ReliableAction.resource_type == action.resource_type,
            ReliableAction.resource_id == action.resource_id,
            ReliableAction.idempotency_key == action.idempotency_key,
            ReliableAction.action.in_(dependencies),
        )
    ).all()
    id_rows = db.scalars(
        select(ReliableAction).where(
            ReliableAction.id.in_(dependency_ids),
            ReliableAction.resource_type == action.resource_type,
            ReliableAction.resource_id == action.resource_id,
        )
    ).all()
    named_states = {row.action: row.state for row in named_rows}
    id_states = {row.id: row.state for row in id_rows}
    if len(id_states) != len(set(dependency_ids)):
        raise DeliveryError("invalid_payload")
    states = [named_states.get(name) for name in dependencies] + [
        id_states.get(action_id) for action_id in dependency_ids
    ]
    if "needs_attention" in states:
        return "failed"
    if any(state != "succeeded" for state in states):
        return "waiting"
    return "ready"


def _action_marker(action: ReliableAction) -> str:
    return f"{_ACTION_MARKER_PREFIX}{action.id}"


def _signed_text(
    db: Session,
    action: ReliableAction,
    actor: User,
    issue: dict,
    body: str,
) -> str:
    context = tracker_signatures.build_signature_context(db, actor, issue)
    signed = tracker_signatures.format_signed_comment(
        body=body,
        park_name=context.park_name,
        mechanic_login=context.mechanic_login,
        operator_login=context.operator_login,
        actor_login=context.actor_login,
        occurred_at=datetime.fromtimestamp(action.created_at, UTC),
    )
    return f"{signed}\n\n{_action_marker(action)}"


def _external_id(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("id") or result.get("longId") or "")
    return ""


def _reconciled_external_id(
    action: ReliableAction,
    *,
    token: str,
) -> str | None:
    marker = _action_marker(action)
    comments = tracker_client.list_comments(token=token, key=action.resource_id)
    matches = [
        str(comment.get("id") or "").strip()
        for comment in comments
        if marker in str(comment.get("text") or "")
    ]
    external_ids = {external_id for external_id in matches if external_id}
    if len(external_ids) == 1:
        return next(iter(external_ids))
    if len(external_ids) > 1:
        raise DeliveryError("duplicate_remote_action")
    return None


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
        text=_signed_text(db, action, actor, issue, message.text),
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
    if purpose != "close" and (tracker_issue_is_closed(issue) or target_status_reached(issue, "close")):
        raise DeliveryError("task_already_closed")
    if purpose == "start" and not issue.get("components"):
        _set_issue_field(
            token=token,
            key=action.resource_id,
            field_id="components",
            value=["ROBOT_SUSPENSION"],
        )
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


def _deliver_campaign_review(
    action: ReliableAction, *, token: str, issue: dict
) -> dict[str, str | bool]:
    # Campaign results prefer operator review, but some Tracker queues expose
    # only diagnostics. A fresh issue read makes a timeout after remote success
    # safe to replay without sending the transition a second time.
    for purpose in ("review", "diagnostics"):
        if target_status_reached(issue, purpose):
            return {"already_applied": True, "transition_state": purpose}
    transitions = tracker_client.list_transitions(token=token, key=action.resource_id)
    for purpose in ("review", "diagnostics"):
        transition_id = resolve_transition(transitions, purpose)
        if transition_id is not None:
            tracker_client.transition_issue(
                token=token, key=action.resource_id, transition=transition_id
            )
            return {"transition": transition_id, "transition_state": purpose}
    raise DeliveryError("tracker_transition_missing")


def _deliver_action(db: Session, action: ReliableAction) -> dict[str, Any]:
    actor = db.get(User, action.actor_user_id)
    if actor is None:
        raise DeliveryError("invalid_payload")
    try:
        payload = json.loads(action.payload_json)
    except (TypeError, ValueError) as exc:
        raise DeliveryError("invalid_payload") from exc
    if not isinstance(payload, dict):
        raise DeliveryError("invalid_payload")
    if action.action == "campaign_review":
        submission = _campaign_submission(db, action)
        if submission is None:
            # A newer submission superseded this result while Tracker was offline.
            return {"transition_state": "cancelled"}
        report = db.get(Report, submission.report_id)
        if report is None or report.status != "open":
            # The operator returned or closed the result before the delayed send.
            return {"transition_state": "cancelled"}

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise DeliveryError("authentication")

    if action.action in {"comment", "attach"}:
        external_id = _reconciled_external_id(action, token=token)
        if external_id is not None:
            result: dict[str, Any] = {"external_id": external_id}
            if action.action == "attach":
                temp_id = _attachment_temp_id(action)
                if temp_id is not None:
                    result["attachment_id"] = temp_id
            return result

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
            text=_signed_text(db, action, actor, issue, body),
        )
        return {"external_id": _external_id(result)}
    if action.action == "attach":
        return _deliver_attachment(db, action, token=token, issue=issue, actor=actor)
    if action.action == "campaign_review":
        return _deliver_campaign_review(action, token=token, issue=issue)
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
        if action.action in {"comment", "attach"} and action.result_json:
            result = json.loads(action.result_json)
            external_id = str(result.get("external_id") or "").strip()
            assign_external_id = action.action == "comment" or message.external_id is None
            if external_id:
                if assign_external_id:
                    canonical_conflict = db.scalar(
                        select(TaskMessage).where(
                            TaskMessage.issue_key == action.resource_id,
                            TaskMessage.kind == message.kind,
                            TaskMessage.external_id == external_id,
                            TaskMessage.id != message.id,
                        )
                    )
                    if canonical_conflict is not None:
                        mark_needs_attention(
                            db,
                            action,
                            error_code="timeline_external_id_conflict",
                        )
                        message.sync_state = "needs_attention"
                        message.updated_at = action.updated_at
                        return
                tracker_twin = db.scalar(
                    select(TaskMessage).where(
                        TaskMessage.issue_key == action.resource_id,
                        TaskMessage.kind == "tracker",
                        TaskMessage.external_id == external_id,
                    )
                )
                try:
                    with db.begin_nested():
                        if tracker_twin is not None:
                            attachments = db.scalars(
                                select(TaskAttachment).where(
                                    TaskAttachment.message_id == tracker_twin.id
                                )
                            ).all()
                            for attachment in attachments:
                                attachment.message_id = message.id
                            db.flush()
                            db.delete(tracker_twin)
                        if assign_external_id:
                            message.external_id = external_id
                        db.flush()
                except IntegrityError:
                    mark_needs_attention(
                        db,
                        action,
                        error_code="timeline_external_id_conflict",
                    )
                    message.sync_state = "needs_attention"
                    message.updated_at = action.updated_at
    elif action.state == "needs_attention":
        message.sync_state = "needs_attention"
    else:
        message.sync_state = "pending"
    message.updated_at = action.updated_at


def _campaign_submission(db: Session, action: ReliableAction) -> CampaignSubmission | None:
    if action.action != "campaign_review":
        return None
    try:
        payload = json.loads(action.payload_json)
        submission_id = payload["campaign_submission_id"]
        report_id = payload["report_id"]
        if not isinstance(submission_id, int) or not isinstance(report_id, int):
            return None
    except (TypeError, ValueError, KeyError):
        return None
    submission = db.get(CampaignSubmission, submission_id)
    # A returned result may have been resubmitted with a new report. An old
    # outbox action must never overwrite that new cycle's sync state.
    return submission if submission is not None and submission.report_id == report_id else None


def _sync_campaign_submission(db: Session, action: ReliableAction) -> bool:
    submission = _campaign_submission(db, action)
    if submission is None:
        return False
    if action.state == "succeeded":
        result = json.loads(action.result_json or "{}")
        submission.tracker_transition = str(result.get("transition_state") or "failed")
    elif action.state == "needs_attention":
        submission.tracker_transition = "failed"
    else:
        submission.tracker_transition = "pending"
    return True


def _audit_campaign_action(db: Session, action: ReliableAction) -> None:
    if action.state not in {"succeeded", "needs_attention"}:
        return
    submission = _campaign_submission(db, action)
    if submission is None:
        return
    if submission.tracker_transition == "cancelled":
        return
    audit.record(
        db,
        action=audit.ACTION_TRACKER_TRANSITION,
        actor=db.get(User, action.actor_user_id),
        park_id=submission.park_id,
        target_type="tracker_issue",
        target_id=action.resource_id,
        outcome=(audit.OUTCOME_SUCCESS if action.state == "succeeded" else audit.OUTCOME_FAILURE),
        detail=(f"campaign={submission.campaign_id}; transition={submission.tracker_transition}"),
    )


def _process_batch(session_factory) -> int:
    with session_factory() as db:
        actions = claim_due_batch(db)
        for action in actions:
            try:
                dependency_state = _dependency_state(db, action)
                if dependency_state == "failed":
                    mark_needs_attention(db, action, error_code="prerequisite_failed")
                    db.commit()
                    continue
                if dependency_state == "waiting":
                    action.state = "pending"
                    action.lease_until = None
                    action.next_attempt_at = time.time()
                    action.updated_at = time.time()
                    db.commit()
                    continue
                result = _deliver_action(db, action)
                complete_action(db, action, result)
                _sync_message(db, action)
                _sync_campaign_submission(db, action)
                db.commit()
                _audit_campaign_action(db, action)
                tracker_cache.invalidate_issue(action.resource_id)
            except DeliveryError as exc:
                mark_needs_attention(db, action, error_code=exc.code)
                _sync_message(db, action)
                _sync_campaign_submission(db, action)
                db.commit()
                _audit_campaign_action(db, action)
            except tracker_client.TrackerError as exc:
                schedule_retry(db, action, error_code=_tracker_error_code(exc))
                _sync_message(db, action)
                _sync_campaign_submission(db, action)
                db.commit()
                _audit_campaign_action(db, action)
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
