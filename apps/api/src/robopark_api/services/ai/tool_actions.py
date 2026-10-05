"""One durable tool step per worker tick; confirmation is never a model tool."""

import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select, update

from robopark_api.ai_models import AIAction, AIJob
from robopark_api.models import User
from robopark_api.services.ai import knowledge, policy, runtime, tool_domain
from robopark_api.services.database_locks import database_idempotency_lock

MAX_ACTIONS = 6
CONFIRM_SECONDS = 900
ACTIVE = {"ready", "waiting", "approved", "running"}


def rows(db, job_id):
    return list(
        db.scalars(select(AIAction).where(AIAction.job_id == job_id).order_by(AIAction.ordinal))
    )


def authorize_views(db, user, job):
    seen = set()
    scope_cache = {}
    for row in rows(db, job.id):
        key = (row.tool, json.dumps(row.arguments, sort_keys=True))
        if key not in seen:
            tool_domain.authorize_view(
                db, user, job.park_id, row.tool, row.arguments, scope_cache=scope_cache
            )
            seen.add(key)


def views(db, user, job):
    result = []
    scope_cache = {}
    for row in rows(db, job.id):
        value = {
            "id": row.id,
            "tool": row.tool,
            "state": row.state,
            "error": row.error,
            "created_at": datetime.fromtimestamp(row.created_at, UTC).isoformat(),
        }
        try:
            tool_domain.authorize_view(
                db, user, row.park_id, row.tool, row.arguments, scope_cache=scope_cache
            )
            value.update(
                preview=row.preview,
                arguments=row.arguments,
                result=row.result,
                digest=row.digest if row.state == "waiting" else None,
                expires_at=datetime.fromtimestamp(row.expires_at, UTC).isoformat(),
            )
        except HTTPException:
            value.update(
                preview="Данные действия больше недоступны.",
                arguments=None,
                result=None,
                digest=None,
                expires_at=None,
            )
        result.append(value)
    return result


def _authorize(db, settings, job):
    from robopark_api.services.ai import jobs

    if job is None or job.state != "running":
        raise HTTPException(409, "ai_action_cancelled")
    user = db.get(User, job.owner_id, populate_existing=True)
    jobs._authorize_local(db, user, job)
    policy.available(db, settings, ready=True)
    if not jobs.sources_valid(db, user, job.payload.get("sources", [])):
        raise HTTPException(409, "ai_sources_changed")
    return user


def _digest(row):
    payload = [
        row.id,
        row.owner_id,
        row.park_id,
        row.tool,
        row.arguments,
        row.expected,
        row.expires_at,
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _error(exc):
    code = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
    return code if re.fullmatch(r"[a-z][a-z0-9_]{1,119}", code) else "ai_action_failed"


def redact_value(value):
    if isinstance(value, dict):
        return {
            key: "[скрыто]"
            if re.fullmatch(r"(?i)(password|token|authorization|secret|api[_-]?key)", key)
            else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return knowledge.redact(value) if isinstance(value, str) else value


def _append_result(db, job, messages, row):
    output = json.dumps(
        {"action_id": row.id, "state": row.state, "result": row.result}, ensure_ascii=False
    )
    job.payload = {
        **job.payload,
        "tool_messages": [
            *messages,
            {
                "role": "tool",
                "tool_call_id": messages[-1]["tool_calls"][0]["id"],
                "content": output,
            },
        ],
    }
    job.state, job.updated_at = "queued", time.time()
    db.commit()


def _plan(session_factory, settings, job_id, messages, response):
    call = response["tool_calls"][0]
    name = call["function"]["name"]
    arguments = json.loads(call["function"]["arguments"])
    with session_factory() as db:
        job = db.get(AIJob, job_id)
        user = _authorize(db, settings, job)
        prepared = tool_domain.prepare(db, user, job.park_id, name, arguments)
        canonical = prepared["arguments"]
        db.rollback()
        with database_idempotency_lock(db, "ai-controls"):
            job = db.get(AIJob, job_id, populate_existing=True)
            user = _authorize(db, settings, job)
            previous = rows(db, job.id)
            turns = job.payload.get("tool_turns", 0)
            if turns >= MAX_ACTIONS:
                raise HTTPException(409, "ai_tool_limit")
            transcript = [*messages, response]
            job.payload = {**job.payload, "tool_messages": transcript, "tool_turns": turns + 1}
            # A looping model must not duplicate even a successful write.
            duplicate = next(
                (
                    item
                    for item in previous
                    if name not in {"task_get", "robot_check", "script_list", "script_get"}
                    and item.tool == name
                    and item.arguments == canonical
                ),
                None,
            )
            if duplicate:
                if duplicate.state != "succeeded":
                    raise HTTPException(409, "ai_action_uncertain")
                _append_result(db, job, transcript, duplicate)
                return
            row = AIAction(
                id=str(uuid4()),
                job_id=job.id,
                owner_id=user.id,
                park_id=job.park_id,
                ordinal=len(previous),
                call_id=call["id"],
                tool=name,
                arguments=canonical,
                expected=prepared["expected"],
                preview=knowledge.redact(prepared["preview"])[:4000],
                state="waiting"
                if name in {"task_close", "script_delete"} or prepared["confirmation_required"]
                else "ready",
                expires_at=time.time() + CONFIRM_SECONDS,
            )
            row.digest = _digest(row)
            db.add(row)
            job.state, job.updated_at = (
                ("waiting" if row.state == "waiting" else "queued"),
                time.time(),
            )
            db.commit()
            policy.changed(db, user, "action_planned", row.id)


def _execute(session_factory, settings, job_id, action_id):
    # Commit the receipt before crossing the side-effect boundary. An interrupted
    # action is never put back in the ready queue by startup recovery.
    with session_factory() as db:
        job = db.get(AIJob, job_id)
        user = _authorize(db, settings, job)
        row = db.get(AIAction, action_id)
        prepared = tool_domain.prepare(db, user, job.park_id, row.tool, row.arguments)
        if prepared["expected"] != row.expected or prepared["arguments"] != row.arguments:
            raise HTTPException(409, "ai_action_changed")
        db.rollback()
        with database_idempotency_lock(db, "ai-controls"):
            job = db.get(AIJob, job_id, populate_existing=True)
            user = _authorize(db, settings, job)
            row = db.get(AIAction, action_id, populate_existing=True)
            if row.state not in {"ready", "approved"}:
                raise HTTPException(409, "ai_action_cancelled")
            if (
                row.tool in {"task_close", "script_delete"} or prepared["confirmation_required"]
            ) and row.state != "approved":
                raise HTTPException(409, "ai_confirmation_required")
            if row.expires_at < time.time():
                raise HTTPException(409, "ai_confirmation_expired")
            row.state, row.updated_at = "running", time.time()
            db.commit()
        try:
            user = _authorize(db, settings, db.get(AIJob, job_id, populate_existing=True))
            value = tool_domain.execute(
                db,
                settings,
                user,
                row.park_id,
                row.tool,
                row.arguments,
                idempotency_key=row.id,
                expected=row.expected,
            )
            # Domain commands return JSON. Redact text before retaining or passing
            # it to the model; never retain arbitrary exception/debug output.
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode()) > 24000:
                raise ValueError("ai_tool_result_too_large")
            result = redact_value(value)
        except Exception as exc:
            db.rollback()
            row = db.get(AIAction, action_id, populate_existing=True)
            row.state, row.error, row.updated_at = "uncertain", _error(exc), time.time()
            job = db.get(AIJob, job_id, populate_existing=True)
            if job:
                job.state, job.error, job.updated_at = "failed", "ai_action_uncertain", time.time()
            db.commit()
            return
        row = db.get(AIAction, action_id, populate_existing=True)
        row.state, row.result, row.updated_at = "succeeded", result, time.time()
        job = db.get(AIJob, job_id, populate_existing=True)
        # Deletion/cancellation refuses running actions, so the owning job must
        # still exist. Publication/next inference rechecks actor and source scope.
        if job is not None and job.state == "running":
            _append_result(db, job, job.payload["tool_messages"], row)
        else:
            db.commit()
        policy.changed(db, user, "action_succeeded", row.id)


def step(session_factory, settings, job_id, messages):
    """Return a final text result, or None after durably scheduling the next step."""
    with session_factory() as db:
        job = db.get(AIJob, job_id)
        user = _authorize(db, settings, job)
        authorize_views(db, user, job)
        pending = next(
            (row.id for row in rows(db, job_id) if row.state in {"ready", "approved"}), None
        )
        definitions = tool_domain.catalog(db, user)
    if pending:
        try:
            _execute(session_factory, settings, job_id, pending)
        except HTTPException as exc:
            with session_factory() as db:
                row = db.get(AIAction, pending)
                if row and row.state in {"ready", "approved"}:
                    row.state, row.error, row.updated_at = "failed", _error(exc), time.time()
                    db.commit()
            raise
        return None
    count, capacity = runtime.context_tokens(settings, messages, tools=definitions)
    if count + 1400 > capacity:
        raise runtime.RuntimeFailure("ai_context_too_large")
    response = runtime.complete_turn(settings, messages, definitions)
    if response.get("tool_calls"):
        _plan(session_factory, settings, job_id, messages, response)
        return None
    return {"content": knowledge.redact(response["content"])}


def confirm(db, settings, user, action_id, digest):
    from robopark_api.services.ai import jobs

    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIAction, action_id)
        if row.owner_id != user.id or row.job_id is None:
            raise HTTPException(404, "ai_not_found")
        job = policy.get_row(db, AIJob, row.job_id)
        jobs._authorize_local(db, user, job)
        policy.available(db, settings, ready=True)
        if not hmac.compare_digest(row.digest, digest):
            raise HTTPException(409, "ai_confirmation_changed")
        if row.state in {"approved", "running", "succeeded"}:
            return job
        if row.state != "waiting" or job.state != "waiting":
            raise HTTPException(409, "ai_action_cancelled")
        if row.expires_at < time.time():
            raise HTTPException(409, "ai_confirmation_expired")
        row.state, row.updated_at = "approved", time.time()
        job.state, job.updated_at = "queued", time.time()
        db.commit()
        policy.changed(db, user, "action_confirmed", row.id)
        return job


def cancel_pending(db, job_ids):
    if db.scalar(
        select(AIAction.id)
        .where(AIAction.job_id.in_(job_ids), AIAction.state == "running")
        .limit(1)
    ):
        raise HTTPException(409, "ai_action_in_progress")
    db.execute(
        update(AIAction)
        .where(AIAction.job_id.in_(job_ids), AIAction.state.in_(ACTIVE))
        .values(state="cancelled", updated_at=time.time())
    )


def recover(db):
    db.execute(
        update(AIAction)
        .where(AIAction.state == "running")
        .values(state="uncertain", error="ai_action_interrupted", updated_at=time.time())
    )


def maintain(db):
    now = time.time()
    expired = select(AIAction.job_id).where(AIAction.state == "waiting", AIAction.expires_at < now)
    db.execute(
        update(AIJob)
        .where(AIJob.id.in_(expired), AIJob.state == "waiting")
        .values(state="cancelled", error="ai_confirmation_expired", updated_at=now)
    )
    db.execute(
        update(AIAction)
        .where(AIAction.state == "waiting", AIAction.expires_at < now)
        .values(state="cancelled", error="ai_confirmation_expired", updated_at=now)
    )
    db.execute(
        delete(AIAction).where(
            AIAction.job_id.is_(None),
            AIAction.state.not_in(ACTIVE),
            AIAction.updated_at < now - 30 * 86400,
        )
    )
