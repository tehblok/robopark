"""Local assistant with live tools. Management is a separate RBAC boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select

from robopark_api import ai_schemas as S
from robopark_api.ai_models import AIConfig, AIConversation, AIJob, AIMessage, AIPrompt
from robopark_api.config import get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.services.ai import (
    issue_context,
    jobs,
    knowledge,
    policy,
    prompts,
    runtime,
    tool_actions,
)
from robopark_api.services.database_locks import database_idempotency_lock


def staff(db=Depends(get_db), user=Depends(require_user)):
    policy.staff(db, user)
    return user


def supported(user=Depends(staff), settings=Depends(get_settings)):
    policy.hardware(settings)
    return user


def manager(user=Depends(supported), db=Depends(get_db)):
    policy.manager(db, user)
    return user


def private_response(response: Response):
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/ai", tags=["local-ai"], dependencies=[Depends(private_response)])
DB = Annotated[object, Depends(get_db)]
Settings = Annotated[object, Depends(get_settings)]
Staff = Annotated[object, Depends(staff)]
Supported = Annotated[object, Depends(supported)]
Manager = Annotated[object, Depends(manager)]


def status_value(db, settings, user):
    status = policy.host_status(settings)
    counts = {"documents": 0, "candidates": 0, "jobs": 0}
    if status["supported"]:
        counts["jobs"] = db.scalar(
            select(func.count())
            .select_from(AIJob)
            .where(AIJob.owner_id == user.id, AIJob.state.in_(("queued", "running", "waiting")))
        )
        if not policy.config(db)["enabled"]:
            status.update(enabled=False, ready=False, reason="ai_disabled")
    return {
        **status,
        "can_manage": policy.can_manage(db, user),
        "counts": counts,
    }


@router.get("/status")
def status(db: DB, settings: Settings, user: Staff):
    return status_value(db, settings, user)


@router.get("/config")
def config(db: DB, user: Manager):
    return policy.config(db)


@router.patch("/config")
def update_config(value: S.ConfigUpdate, db: DB, user: Manager):
    if value.learning_enabled is True:
        raise HTTPException(410, "ai_knowledge_removed")
    with database_idempotency_lock(db, "ai-controls"):
        row = db.get(AIConfig, 1)
        if row is None:
            row = AIConfig(id=1)
            db.add(row)
            db.flush()
        policy.cas(
            db, row, value.revision, value.model_dump(exclude_none=True, exclude={"revision"})
        )
        result = policy.config(db)
    policy.changed(db, user, "config", "global")
    return result


@router.post("/runtime")
def control(value: S.RuntimeIn, db: DB, settings: Settings, user: Manager):
    with database_idempotency_lock(db, "ai-controls"):
        db.commit()
        try:
            runtime.broker(settings, "/control", value.model_dump(), timeout=20)
        except runtime.RuntimeFailure:
            raise HTTPException(503, "ai_runtime_unavailable") from None
    policy.changed(db, user, "runtime_" + value.action, "host")
    return status_value(db, settings, user)


@router.get("/prompts")
def list_prompts(db: DB, user: Manager):
    return prompts.listing(db)


@router.put("/prompts/{role}")
def update_prompt(role: str, value: S.PromptUpdate, db: DB, user: Manager):
    if role not in prompts.DEFAULTS:
        raise HTTPException(404, "ai_not_found")
    with database_idempotency_lock(db, "ai-controls"):
        row = db.get(AIPrompt, role)
        if row is None:
            row = AIPrompt(role=role, content=prompts.DEFAULTS[role])
            db.add(row)
            db.flush()
        policy.cas(db, row, value.revision, {"content": knowledge.redact(value.content)})
        result = policy.public_columns(row)
    policy.changed(db, user, "prompt", role)
    return result


@router.api_route("/documents", methods=["GET", "POST"])
def retired_documents(user: Supported):
    raise HTTPException(410, "ai_knowledge_removed")


@router.api_route("/documents/{document_id}", methods=["GET", "POST", "PATCH", "DELETE"])
def retired_document(document_id: str, user: Supported):
    raise HTTPException(410, "ai_knowledge_removed")


@router.get("/conversations")
def conversations(db: DB, user: Supported):
    return [
        policy.public_columns(row, ("owner_id",))
        for row in db.scalars(
            select(AIConversation)
            .where(
                AIConversation.owner_id == user.id,
                AIConversation.park_id.in_(policy.parks(db, user)),
            )
            .order_by(AIConversation.updated_at.desc())
            .limit(100)
        )
    ]


@router.post("/conversations", status_code=201)
def create_conversation(value: S.ConversationIn, db: DB, settings: Settings, user: Supported):
    policy.available(db, settings)
    policy.park(db, user, value.park_id)
    issue_context.load(db, user, value.issue_key, value.park_id)
    row = AIConversation(owner_id=user.id, **value.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return policy.public_columns(row, ("owner_id",))


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, db: DB, user: Supported):
    row = jobs.conversation(db, user, conversation_id)
    messages = list(
        db.scalars(
            select(AIMessage)
            .where(AIMessage.conversation_id == row.id)
            .order_by(AIMessage.created_at.desc(), AIMessage.id.desc())
            .limit(100)
        )
    )[::-1]
    active_jobs = list(
        db.scalars(
            select(AIJob)
            .where(AIJob.conversation_id == row.id)
            .order_by(AIJob.created_at.desc())
            .limit(20)
        )
    )
    message_ids = [message.id for message in messages if message.role == "assistant"]
    scope_cache = {("message_job", message_id): None for message_id in message_ids}
    if message_ids:
        for job in db.scalars(
            select(AIJob).where(
                AIJob.conversation_id == row.id,
                AIJob.result["message_id"].as_string().in_(message_ids),
            )
        ):
            scope_cache[("message_job", job.result["message_id"])] = job
    # Prioritize the current action cards and newest answers when the bounded
    # request-local authorization budget cannot cover all old remote objects.
    job_views = [jobs.view(job, db=db, user=user, scope_cache=scope_cache) for job in active_jobs]
    message_views = [
        jobs.visible_message(db, user, msg, scope_cache=scope_cache) for msg in reversed(messages)
    ][::-1]
    return {
        **policy.public_columns(row, ("owner_id",)),
        "messages": message_views,
        "jobs": job_views,
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: DB, user: Supported):
    with database_idempotency_lock(db, "ai-controls"):
        row = jobs.conversation(db, user, conversation_id)
        tool_actions.cancel_pending(db, select(AIJob.id).where(AIJob.conversation_id == row.id))
        db.execute(delete(AIJob).where(AIJob.conversation_id == row.id))
        db.execute(delete(AIMessage).where(AIMessage.conversation_id == row.id))
        db.delete(row)
        db.commit()
    return {"deleted": True}


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def send_message(
    conversation_id: str, value: S.MessageIn, db: DB, settings: Settings, user: Supported
):
    policy.available(db, settings, ready=True)
    row = jobs.conversation(db, user, conversation_id)
    content = knowledge.redact(value.content)
    if not content:
        raise HTTPException(422, "ai_message_empty")
    return jobs.view(
        jobs.enqueue(
            db,
            user,
            kind="chat",
            conversation_id=row.id,
            park_id=row.park_id,
            payload={"content": content, **({"use_tools": True} if value.use_tools else {})},
            key=value.idempotency_key,
        )
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: DB, user: Supported):
    row = policy.get_row(db, AIJob, job_id)
    jobs.authorize(db, user, row)
    value = jobs.view(row, db=db, user=user)
    if not jobs.sources_valid(db, user, row.payload.get("sources", [])):
        value.update(result=None, error="ai_sources_changed")
    return value


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: DB, user: Supported):
    import time

    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIJob, job_id)
        jobs.authorize(db, user, row)
        if row.state in {"queued", "running", "waiting"}:
            tool_actions.cancel_pending(db, [row.id])
            row.state, row.updated_at = "cancelled", time.time()
            db.commit()
        return jobs.view(row, db=db, user=user)


@router.post("/actions/{action_id}/confirm")
def confirm_action(
    action_id: str, value: S.ActionConfirmation, db: DB, settings: Settings, user: Supported
):
    row = tool_actions.confirm(db, settings, user, action_id, value.digest)
    return jobs.view(row, db=db, user=user)
