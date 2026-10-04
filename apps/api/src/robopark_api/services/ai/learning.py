"""Confirmed, operator-reviewed repairs become attributed experience, never policy."""

import hashlib
import json
import logging

from sqlalchemy import select

from robopark_api.ai_models import AIDocument, AIEvent
from robopark_api.services.ai import automations, knowledge, policy
from robopark_api.task_workflow_models import ReliableAction

logger = logging.getLogger(__name__)


def stage_verified_close(db, *, review, park_id, event_key, closed_at, previous_closure=None):
    if review is None or review.reviewer_user_id is None or review.state != "closed":
        return
    primary = db.scalar(
        select(ReliableAction)
        .where(
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == review.issue_key,
            ReliableAction.action == "review",
            ReliableAction.actor_user_id == review.actor_user_id,
            ReliableAction.state == "succeeded",
            ReliableAction.created_at > (previous_closure or 0),
            ReliableAction.created_at <= closed_at,
        )
        .order_by(ReliableAction.created_at.desc())
        .limit(1)
    )
    if primary is None or db.get(AIEvent, event_key):
        return
    approval = db.scalar(
        select(ReliableAction)
        .where(
            ReliableAction.resource_type == "tracker_issue",
            ReliableAction.resource_id == review.issue_key,
            ReliableAction.action == "close",
            ReliableAction.actor_user_id == review.reviewer_user_id,
            ReliableAction.state == "succeeded",
            ReliableAction.created_at >= review.created_at,
            ReliableAction.created_at > (previous_closure or 0),
            ReliableAction.created_at <= closed_at,
        )
        .limit(1)
    )
    if approval is None:
        return
    park_id = park_id or json.loads(approval.payload_json).get("park_id")
    if park_id is None:
        return
    payload = json.loads(primary.payload_json)
    fields = payload.get("repair_fields") or {}
    comment = knowledge.redact(payload.get("comment") or "")[:12000]
    components = fields.get("component_ids", [])
    defect = payload.get("defect_code") or fields.get("defect_code")
    if not comment and not fields:
        return
    # Only an allowlist of observed fields enters future prompts/integrations.
    db.add(
        AIEvent(
            key=event_key,
            park_id=park_id,
            occurred_at=closed_at,
            payload={
                "event_key": event_key,
                "issue_key": review.issue_key,
                "park_id": park_id,
                "component_ids": components,
                "defect_code": defect,
                "solution_method": fields.get("solution_method"),
                "comment": comment,
            },
        )
    )


def stage_safely(db, **kwargs):
    # An optional knowledge feature must never prevent an actual repair close.
    # Savepoint keeps closure and event atomic when valid, isolates a malformed
    # historical report when invalid, and never exposes report text in logs.
    try:
        with db.begin_nested():
            stage_verified_close(db, **kwargs)
    except Exception:
        logger.warning("AI repair event could not be staged")


def process_events(db, settings):
    if not policy.host_status(settings)["supported"] or not policy.config(db)["enabled"]:
        return
    events = list(
        db.scalars(
            select(AIEvent)
            .where(AIEvent.processed.is_(False))
            .order_by(AIEvent.occurred_at)
            .limit(20)
        )
    )
    for event in events:
        if policy.config(db)["learning_enabled"]:
            ref = "repair:" + event.key
            source_key = hashlib.sha256(
                (str(event.park_id) + "\nticket\n" + ref).encode()
            ).hexdigest()
            if db.scalar(select(AIDocument.id).where(AIDocument.source_key == source_key)) is None:
                content = (
                    "Наблюдавшийся ремонт, проверенный оператором. Не универсальная инструкция.\n"
                    + json.dumps(event.payload, ensure_ascii=False)
                )
                row = AIDocument(
                    source_key=source_key,
                    fingerprint=hashlib.sha256(content.encode()).hexdigest(),
                    title="Опыт ремонта " + event.payload["issue_key"],
                    content=content,
                    kind="ticket",
                    state="active",
                    trust="experience",
                    park_id=event.park_id,
                    source_ref=ref,
                    created_by=None,
                )
                db.add(row)
                db.flush()
                knowledge.reindex(db, row)
        automations.stage_runs(db, event)
        event.processed = True
    db.commit()
