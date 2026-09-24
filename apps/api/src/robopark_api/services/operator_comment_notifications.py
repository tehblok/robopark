"""Complete keyed operator-comment notification intent after Tracker success."""

import json

from sqlalchemy.orm import Session

from robopark_api.routers.push import PushService
from robopark_api.task_workflow_models import ReliableAction


def stage_if_intended(
    db: Session, action: ReliableAction, *, push_service: PushService | None = None
) -> None:
    """Idempotently stage the snapshotted audience in the action transaction."""
    payload = json.loads(action.payload_json)
    intent = payload.get("_notification_intent")
    if intent is None:
        return
    if not isinstance(intent, dict) or intent.get("event_type") != "operator_comment":
        raise ValueError("invalid_notification_intent")
    recipient_ids = intent.get("recipient_user_ids")
    if not isinstance(recipient_ids, list) or not all(
        isinstance(user_id, int) for user_id in recipient_ids
    ):
        raise ValueError("invalid_notification_intent")
    (push_service or PushService(lambda: db)).emit_in_transaction(
        db,
        event_type="operator_comment",
        park_id=intent["park_id"],
        protected_text=intent["protected_text"],
        recipient_user_ids=set(recipient_ids),
        event_key=f"operator-comment:{action.resource_id}:{action.id}",
    )
    db.flush()
