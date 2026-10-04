"""Durable bounded queue; only the exclusive background worker runs inference."""

import asyncio
import json
import logging
import re
import time
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update

from robopark_api.ai_models import AIConversation, AIDocument, AIJob, AIMessage, AIRun, AIScript
from robopark_api.models import User
from robopark_api.services.ai import (
    automations,
    bundle,
    issue_context,
    knowledge,
    learning,
    policy,
    prompts,
    runtime,
)
from robopark_api.services.database_locks import database_idempotency_lock
from robopark_api.services.ops.maintenance import host_maintenance_active

logger = logging.getLogger(__name__)
FINAL = {"succeeded", "failed", "cancelled"}


def view(row):
    value = policy.columns(
        row, ("owner_id", "conversation_id", "park_id", "idempotency_key", "payload")
    )
    for key in ("created_at", "updated_at"):
        value[key] = datetime.fromtimestamp(value[key], UTC).isoformat()
    return value


def conversation(db, user, row_id):
    row = policy.get_row(db, AIConversation, row_id)
    if row.owner_id != user.id:
        raise HTTPException(404, "ai_not_found")
    policy.park(db, user, row.park_id)
    issue_context.load(db, user, row.issue_key, row.park_id)
    return row


def authorize(db, user, job):
    if user is None or job.owner_id != user.id:
        raise HTTPException(404, "ai_not_found")
    policy.staff(db, user)
    if job.kind != "chat":
        policy.manager(db, user)
    if job.park_id is not None:
        policy.park(db, user, job.park_id)
    if job.conversation_id:
        conversation(db, user, job.conversation_id)


def sources_valid(db, user, sources):
    for source in sources:
        try:
            doc = knowledge.get(db, user, source["id"])
        except HTTPException:
            return False
        if doc.state != "active" or doc.revision != source["revision"]:
            return False
    return True


def visible_message(db, user, row):
    value = policy.public_columns(row, ("conversation_id",))
    if row.sources and not sources_valid(db, user, row.sources):
        value.update(
            content="Ответ скрыт: источник изменён или больше недоступен. Задайте вопрос заново.",
            sources=[],
        )
    return value


def enqueue(db, user, *, kind, payload, park_id=None, conversation_id=None, key=None):
    key = key or str(uuid4())
    with database_idempotency_lock(db, "ai-queue"):
        existing = db.scalar(
            select(AIJob).where(AIJob.owner_id == user.id, AIJob.idempotency_key == key)
        )
        if existing:
            if (
                existing.kind != kind
                or existing.conversation_id != conversation_id
                or existing.payload.get("request") != payload
            ):
                raise HTTPException(409, "ai_idempotency_conflict")
            return existing
        total = db.scalar(
            select(func.count()).select_from(AIJob).where(AIJob.state.in_(("queued", "running")))
        )
        own = db.scalar(
            select(func.count())
            .select_from(AIJob)
            .where(AIJob.owner_id == user.id, AIJob.state.in_(("queued", "running")))
        )
        if total >= 50 or own >= 3:
            raise HTTPException(429, "ai_queue_full")
        if conversation_id:
            row = conversation(db, user, conversation_id)
            if db.scalar(
                select(AIJob.id)
                .where(AIJob.conversation_id == row.id, AIJob.state.in_(("queued", "running")))
                .limit(1)
            ):
                raise HTTPException(409, "ai_conversation_busy")
            db.add(
                AIMessage(
                    conversation_id=row.id, role="user", content=payload["content"], sources=[]
                )
            )
            row.updated_at = time.time()
            if row.title == "Новый разговор":
                row.title = payload["content"][:80]
        job = AIJob(
            owner_id=user.id,
            conversation_id=conversation_id,
            park_id=park_id,
            kind=kind,
            payload={"request": payload},
            idempotency_key=key,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job


def _prepare(db, settings, job):
    user = db.get(User, job.owner_id, populate_existing=True)
    authorize(db, user, job)
    policy.available(db, settings, ready=job.kind != "script_test")
    request = job.payload["request"]
    if job.kind == "script_test":
        script = policy.get_row(db, AIScript, request["script_id"])
        if script.revision != request["revision"]:
            raise HTTPException(409, "ai_script_changed")
        return {"source": script.source, "input": request["input"]}, []
    question = request.get("content") or request["instruction"]
    issue_text = ""
    if job.conversation_id:
        conversation_row = db.get(AIConversation, job.conversation_id)
        issue = issue_context.load(db, user, conversation_row.issue_key, conversation_row.park_id)
        issue_text = issue_context.context(db, user, issue)
    sources = knowledge.search(
        db, user, question, context=issue_text[:1200], park_id=job.park_id, limit=3
    )
    for source in sources:
        doc = db.get(AIDocument, source["id"])
        source["revision"] = doc.revision
        source["excerpt"] = source["excerpt"][:2400]
    prior = []
    if job.conversation_id:
        history = list(
            db.scalars(
                select(AIMessage)
                .where(AIMessage.conversation_id == job.conversation_id)
                .order_by(AIMessage.created_at.desc(), AIMessage.id.desc())
                .limit(7)
            )
        )[::-1]
        # Last user message is the queued request. History is bounded separately.
        budget = 2000
        for message in reversed(history[:-1]):
            if len(message.content) > budget or not sources_valid(db, user, message.sources):
                break
            prior.insert(0, {"role": message.role, "content": message.content})
            budget -= len(message.content)
    return prompts.fit_context(
        db,
        user,
        sources,
        question,
        issue=issue_text,
        history=prior,
        draft=request.get("kind") if job.kind == "draft" else None,
        token_count=lambda messages: runtime.context_tokens(settings, messages),
    )


def _draft(content, kind):
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        value = json.loads(clean)
        if not isinstance(value, dict) or not isinstance(value.get("explanation"), str):
            raise ValueError()
        if kind == "script":
            source = value["source"]
            if not isinstance(source, str) or len(source) > 32000:
                raise ValueError()
            automations.validate_script(source)
            return {"source": source, "explanation": knowledge.redact(value["explanation"])}
        proposal = value["proposal"]
        if not isinstance(proposal, dict):
            raise ValueError()
        # Still inert. The normal automation input schema validates the edited
        # proposal when an administrator explicitly saves it.
        return {
            "proposal": automations.bounded_json(proposal, 20000),
            "explanation": knowledge.redact(value["explanation"]),
        }
    except (KeyError, ValueError, TypeError) as exc:
        raise runtime.RuntimeFailure("ai_draft_invalid") from exc


def _validate_claim(db, settings, job, sources):
    """Re-read mutable controls at the serialized execution boundary."""

    actor = db.get(User, job.owner_id, populate_existing=True)
    authorize(db, actor, job)
    policy.available(db, settings, ready=job.kind != "script_test")
    if not sources_valid(db, actor, sources):
        raise HTTPException(409, "ai_sources_changed")
    if job.kind == "script_test":
        request = job.payload["request"]
        script = policy.get_row(db, AIScript, request["script_id"])
        if script.revision != request["revision"]:
            raise HTTPException(409, "ai_script_changed")


def process_job(session_factory, settings):
    with session_factory() as db:
        job = db.scalar(
            select(AIJob)
            .where(AIJob.state == "queued")
            .order_by(AIJob.created_at, AIJob.id)
            .limit(1)
        )
        if job is None:
            return False
        job_id, kind = job.id, job.kind
        try:
            request, sources = _prepare(db, settings, job)
            draft_kind = job.payload["request"].get("kind")
            # Do not retain preparation's read transaction while bounded lock
            # acquisition waits for a concurrent administrative operation.
            db.rollback()
            with database_idempotency_lock(db, "ai-controls"):
                # Preparation can overlap cancel/delete/disable. End its read
                # identity map before deciding whether execution may begin
                # under the shared controls lock.
                job = db.get(AIJob, job_id, populate_existing=True)
                if job is None or job.state != "queued":
                    return True
                _validate_claim(db, settings, job, sources)
                claimed = db.execute(
                    update(AIJob)
                    .where(AIJob.id == job_id, AIJob.state == "queued")
                    .values(
                        payload={**job.payload, "sources": sources},
                        state="running",
                        updated_at=time.time(),
                    )
                    .execution_options(synchronize_session=False)
                ).rowcount
                if claimed != 1:
                    db.rollback()
                    return True
                db.commit()
        except (HTTPException, prompts.ContextTooLarge) as exc:
            if isinstance(exc, HTTPException) and exc.detail == "idempotency_lock_busy":
                db.rollback()
                return True
            error = (
                "ai_context_too_large"
                if isinstance(exc, prompts.ContextTooLarge)
                else str(exc.detail)
            )
            db.execute(
                update(AIJob)
                .where(AIJob.id == job_id, AIJob.state == "queued")
                .values(
                    state="failed" if isinstance(exc, prompts.ContextTooLarge) else "cancelled",
                    error=error,
                    updated_at=time.time(),
                )
            )
            db.commit()
            return True
    try:
        if kind == "script_test":
            result = runtime.broker(settings, "/sandbox", request, timeout=30)
            automations.bounded_json(result)
        else:
            content = knowledge.redact(runtime.complete(settings, request))
            result = _draft(content, draft_kind) if kind == "draft" else {"content": content}
        error = None
    except (runtime.RuntimeFailure, HTTPException) as exc:
        result, error = (
            None,
            str(exc) if isinstance(exc, runtime.RuntimeFailure) else "ai_output_invalid",
        )
    except Exception:
        result, error = None, "ai_execution_failed"
        logger.warning("Local AI execution failed")
    with session_factory() as db, database_idempotency_lock(db, "ai-controls"):
        job = db.get(AIJob, job_id, populate_existing=True)
        if job is None or job.state != "running":
            return True
        try:
            actor = db.get(User, job.owner_id, populate_existing=True)
            authorize(db, actor, job)
            policy.available(db, settings)
            if not sources_valid(db, actor, sources):
                raise HTTPException(409, "ai_sources_changed")
            if error:
                job.state, job.error = "failed", error
            elif kind == "chat":
                # Citations are server-derived; fabricated IDs are stripped.
                ids = {s["id"] for s in sources}
                content = re.sub(
                    r"\[источник:\s*([^\]]+)\]",
                    lambda m: m[0] if m[1].strip() in ids else "",
                    result["content"],
                )
                message = AIMessage(
                    conversation_id=job.conversation_id,
                    role="assistant",
                    content=content,
                    sources=sources,
                )
                db.add(message)
                db.flush()
                db.get(AIConversation, job.conversation_id).updated_at = time.time()
                job.state, job.result = "succeeded", {"message_id": message.id}
            else:
                if kind == "script_test":
                    script = policy.get_row(db, AIScript, job.payload["request"]["script_id"])
                    if script.revision != job.payload["request"]["revision"]:
                        raise HTTPException(409, "ai_script_changed")
                    script.tested_revision = script.revision
                job.state, job.result = "succeeded", result
        except HTTPException as exc:
            job.state, job.error, job.result = "cancelled", str(exc.detail), None
        job.updated_at = time.time()
        db.commit()
    return True


def recover(db):
    db.execute(
        update(AIJob)
        .where(AIJob.state == "running")
        .values(state="failed", error="ai_worker_interrupted", updated_at=time.time())
    )
    db.execute(
        update(AIRun)
        .where(AIRun.state == "running")
        .values(state="uncertain", error="ai_delivery_interrupted", updated_at=time.time())
    )
    db.commit()


def tick(session_factory, settings, *, first=False):
    if host_maintenance_active(settings) or not policy.host_status(settings)["supported"]:
        return False
    with session_factory() as db:
        if first:
            recover(db)
        learning.process_events(db, settings)
    process_job(session_factory, settings)
    automations.process_run(session_factory, settings)
    with session_factory() as db:
        bundle.step(db, settings)
    with session_factory() as db, database_idempotency_lock(db, "ai-controls"):
        learning.purge_event_payloads(db, before=time.time() - 30 * 86400)
        db.commit()
    return True


async def run_loop(session_factory, stop, settings):
    first = True
    while not stop.is_set():
        try:
            if not host_maintenance_active(settings) and await asyncio.to_thread(
                tick, session_factory, settings, first=first
            ):
                first = False
        except Exception:
            # Optional AI failures must not stop Tracker delivery or core jobs.
            # No prompts, private text or exception details enter normal logs.
            logger.warning("Local AI worker iteration failed")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=2)
