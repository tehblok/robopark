"""Deterministic event rules; model output has no execution authority."""

import ast
import json
import re
import time

from fastapi import HTTPException
from sqlalchemy import select

from robopark_api.ai_models import AIAutomation, AIConnector, AIEvent, AIRun, AIScript
from robopark_api.models import User
from robopark_api.services.ai import connectors, policy, runtime
from robopark_api.services.database_locks import database_idempotency_lock

FIELDS = {
    "issue_key",
    "park_id",
    "component_ids",
    "defect_code",
    "solution_method",
    "comment",
    "event_key",
}


def bounded_json(value, limit=65536):
    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > limit:
            raise ValueError()
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, "ai_payload_too_large") from None
    return value


def validate_script(source):
    try:
        tree = ast.parse(source)
        if not any(
            isinstance(node, ast.FunctionDef) and node.name == "main" and len(node.args.args) == 1
            for node in tree.body
        ):
            raise ValueError()
    except (SyntaxError, ValueError, RecursionError):
        raise HTTPException(422, "ai_script_invalid") from None
    # Syntax checking is not a security boundary. Only Docker executes code.


def render(value, event, depth=0):
    if depth > 12:
        raise HTTPException(422, "ai_template_invalid")
    if isinstance(value, str):
        placeholders = re.findall(r"\{\{(.*?)\}\}", value)
        if any(key not in FIELDS for key in placeholders):
            raise HTTPException(422, "ai_template_invalid")
        if len(placeholders) == 1 and value == "{{" + placeholders[0] + "}}":
            return event.get(placeholders[0])
        for key in placeholders:
            replacement = event.get(key)
            value = value.replace(
                "{{" + key + "}}",
                str(replacement)
                if not isinstance(replacement, (list, dict))
                else json.dumps(replacement, ensure_ascii=False),
            )
        return value
    if isinstance(value, list):
        return [render(item, event, depth + 1) for item in value]
    if isinstance(value, dict):
        return {key: render(item, event, depth + 1) for key, item in value.items()}
    return value


def matches(filters, event):
    if not isinstance(event.get("component_ids", []), list):
        raise HTTPException(422, "ai_event_invalid")
    components = set(map(str, event.get("component_ids", [])))
    if filters.get("component_ids") and not components.intersection(filters["component_ids"]):
        return False
    if (
        filters.get("defect_codes")
        and str(event.get("defect_code", "")) not in filters["defect_codes"]
    ):
        return False
    text = str(event.get("comment", "")).casefold()
    return not filters.get("keywords") or any(
        word.casefold() in text for word in filters["keywords"]
    )


def validate_action(db, action, *, enabled=False):
    bounded_json(action)
    render(action.get("body", {}), {})
    if not action.get("connector_id") and not action.get("script_id"):
        raise HTTPException(422, "ai_action_required")
    for key, model in (("connector_id", AIConnector), ("script_id", AIScript)):
        if not action.get(key):
            continue
        row = policy.get_row(db, model, action[key])
        if enabled and not row.enabled:
            raise HTTPException(409, "ai_dependency_disabled")
        if enabled and model is AIScript and row.tested_revision != row.revision:
            raise HTTPException(409, "ai_script_test_required")


def stage_runs(db, event):
    for rule in db.scalars(
        select(AIAutomation).where(
            AIAutomation.enabled.is_(True),
            AIAutomation.park_id == event.park_id,
            AIAutomation.enabled_at <= event.occurred_at,
        )
    ):
        if matches(rule.filters, event.payload) and not db.scalar(
            select(AIRun.id).where(AIRun.automation_id == rule.id, AIRun.event_key == event.key)
        ):
            db.add(AIRun(automation_id=rule.id, event_key=event.key, revision=rule.revision))


def process_run(session_factory, settings):
    # Shared with all administrative changes: after disable/delete succeeds,
    # an old revision cannot begin sending. An already sent request is never
    # retried automatically, including after worker restart.
    with session_factory() as db, database_idempotency_lock(db, "ai-controls"):
        run = db.scalar(
            select(AIRun).where(AIRun.state == "queued").order_by(AIRun.created_at).limit(1)
        )
        if run is None:
            return False
        run_id = run.id
        try:
            policy.available(db, settings)
            rule = policy.get_row(db, AIAutomation, run.automation_id)
            actor = db.get(User, rule.owner_id)
            policy.manager(db, actor)
            policy.park(db, actor, rule.park_id)
            event = policy.get_row(db, AIEvent, run.event_key)
            if (
                not rule.enabled
                or rule.revision != run.revision
                or rule.enabled_at is None
                or event.occurred_at < rule.enabled_at
            ):
                raise HTTPException(409, "ai_rule_changed")
            validate_action(db, rule.action, enabled=True)
            source = (
                db.get(AIScript, rule.action["script_id"]).source
                if rule.action.get("script_id")
                else None
            )
            connector = (
                policy.columns(db.get(AIConnector, rule.action["connector_id"]))
                if rule.action.get("connector_id")
                else None
            )
            payload = render(rule.action.get("body", {}), event.payload)
            event_data = dict(event.payload)
            owner_id, park_id = rule.owner_id, rule.park_id
            run.state = "running"
            run.updated_at = time.time()
            db.commit()
            # End transaction before sandbox/network waits.
            result = {}
            if source:
                sandbox = runtime.broker(
                    settings,
                    "/sandbox",
                    {"source": source, "input": {**event_data, "body": payload}},
                    timeout=30,
                )
                payload = bounded_json(sandbox["output"])
                result["script_completed"] = True
            if connector:
                db.expire_all()
                actor = db.get(User, owner_id, populate_existing=True)
                policy.manager(db, actor)
                policy.park(db, actor, park_id)
                policy.available(db, settings)
                db.commit()
                result.update(
                    connectors.deliver(
                        settings, connector, bounded_json(payload), run_id, event_data
                    )
                )
            else:
                result["output"] = payload
            state, error = "succeeded", None
        except HTTPException as exc:
            state, error, result = "cancelled", str(exc.detail), None
        except connectors.DeliveryFailure as exc:
            state, error, result = "uncertain" if exc.uncertain else "failed", str(exc), None
        except (runtime.RuntimeFailure, KeyError):
            state, error, result = "failed", "ai_sandbox_failed", None
        except Exception:
            state, error, result = "uncertain", "ai_execution_interrupted", None
        row = db.get(AIRun, run_id, populate_existing=True)
        row.state, row.error, row.result, row.updated_at = state, error, result, time.time()
        db.commit()
        return True
