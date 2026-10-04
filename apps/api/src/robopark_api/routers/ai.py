"""Local assistant and scoped knowledge. Management is a separate RBAC boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import delete, func, select

from robopark_api import ai_schemas as S
from robopark_api.ai_models import AIConfig, AIConversation, AIDocument, AIJob, AIMessage, AIPrompt
from robopark_api.config import get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.services.ai import issue_context, jobs, knowledge, policy, prompts, runtime
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
        scope = knowledge.scoped(db, user)
        counts["documents"] = db.scalar(
            select(func.count()).select_from(AIDocument).where(scope, AIDocument.state == "active")
        )
        if policy.can_manage(db, user):
            counts["candidates"] = db.scalar(
                select(func.count())
                .select_from(AIDocument)
                .where(scope, AIDocument.state == "candidate")
            )
        counts["jobs"] = db.scalar(
            select(func.count())
            .select_from(AIJob)
            .where(AIJob.owner_id == user.id, AIJob.state.in_(("queued", "running")))
        )
        if not policy.config(db)["enabled"]:
            status.update(enabled=False, ready=False, reason="ai_disabled")
    return {**status, "can_manage": policy.can_manage(db, user), "counts": counts}


@router.get("/status")
def status(db: DB, settings: Settings, user: Staff):
    return status_value(db, settings, user)


@router.get("/config")
def config(db: DB, user: Manager):
    return policy.config(db)


@router.patch("/config")
def update_config(value: S.ConfigUpdate, db: DB, user: Manager):
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


@router.get("/documents")
def documents(
    db: DB,
    user: Supported,
    q: str = Query(default="", max_length=500),
    park_id: int | None = None,
    state: str | None = None,
    offset: int = Query(default=0, ge=0, le=1000000),
    limit: int = Query(default=30, ge=1, le=100),
):
    return knowledge.list_documents(
        db, user, query=q, park_id=park_id, state=state, offset=offset, limit=limit
    )


@router.post("/documents", status_code=201)
def create_document(value: S.DocumentIn, db: DB, user: Manager):
    with database_idempotency_lock(db, "ai-knowledge"):
        row, created = knowledge.add(db, user, value)
        db.commit()
        if row.state == "deleted":
            raise HTTPException(409, "ai_document_deleted")
        result = knowledge.view(row, content=True)
    if created:
        policy.changed(db, user, "document_created", row.id)
    return result


@router.post("/documents/import")
def import_documents(value: S.ImportIn, db: DB, user: Manager):
    policy.park(db, user, value.park_id, global_allowed=True)
    with database_idempotency_lock(db, "ai-knowledge"):
        counts = {"created": 0, "duplicates": 0, "rejected": 0}
        for item in value.documents:
            data = item.model_dump()
            data.update(
                park_id=value.park_id,
                state="active"
                if (
                    (value.activate_manuals and item.kind == "manual")
                    or (value.activate_unverified and item.kind != "manual")
                )
                else "candidate",
            )
            _, created = knowledge.add(db, user, data)
            counts["created" if created else "duplicates"] += 1
        db.commit()
    policy.changed(db, user, "knowledge_import", "batch")
    return counts


@router.get("/documents/{document_id}")
def document(document_id: str, db: DB, user: Supported):
    return knowledge.view(knowledge.get(db, user, document_id), content=True)


@router.patch("/documents/{document_id}")
def update_document(document_id: str, value: S.DocumentUpdate, db: DB, user: Manager):
    with database_idempotency_lock(db, "ai-knowledge"):
        row = knowledge.edit(db, user, knowledge.get(db, user, document_id), value)
        result = knowledge.view(row, content=True)
    policy.changed(db, user, "document_updated", document_id)
    return result


@router.delete("/documents/{document_id}")
def delete_document(document_id: str, db: DB, user: Manager):
    with database_idempotency_lock(db, "ai-knowledge"):
        knowledge.remove(db, knowledge.get(db, user, document_id))
    policy.changed(db, user, "document_deleted", document_id)
    return {"deleted": True}


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
    return {
        **policy.public_columns(row, ("owner_id",)),
        "messages": [jobs.visible_message(db, user, msg) for msg in messages],
        "jobs": [jobs.view(job) for job in active_jobs],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: DB, user: Supported):
    with database_idempotency_lock(db, "ai-controls"):
        row = jobs.conversation(db, user, conversation_id)
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
            payload={"content": content},
            key=value.idempotency_key,
        )
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: DB, user: Supported):
    row = policy.get_row(db, AIJob, job_id)
    jobs.authorize(db, user, row)
    value = jobs.view(row)
    if not jobs.sources_valid(db, user, row.payload.get("sources", [])):
        value.update(result=None, error="ai_sources_changed")
    return value


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: DB, user: Supported):
    import time

    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIJob, job_id)
        jobs.authorize(db, user, row)
        if row.state in {"queued", "running"}:
            row.state, row.updated_at = "cancelled", time.time()
            db.commit()
        return jobs.view(row)
