"""Explicit administration for integrations and sandboxed automation."""

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select, update

from robopark_api import ai_schemas as S
from robopark_api.ai_models import (
    AIAutomation,
    AIConnector,
    AIConversation,
    AIJob,
    AIMessage,
    AIRun,
    AIScript,
)
from robopark_api.crypto import MissingSecretKeyError, encrypt_secret
from robopark_api.routers.ai import DB, Manager, Settings, private_response
from robopark_api.services.ai import automations, connectors, jobs, knowledge, learning, policy
from robopark_api.services.database_locks import database_idempotency_lock

router = APIRouter(
    prefix="/ai", tags=["local-ai-management"], dependencies=[Depends(private_response)]
)


def connector_view(row):
    return {
        **policy.public_columns(row, ("encrypted_token",)),
        "token_set": bool(row.encrypted_token),
    }


def encrypted_token(value, settings):
    if any(ord(char) < 32 for char in value):
        raise HTTPException(422, "ai_token_invalid")
    try:
        return encrypt_secret(value, settings.secret_key)
    except MissingSecretKeyError:
        raise HTTPException(503, "ai_secret_key_required") from None


def disable_dependencies(db, key, row_id):
    # JSON extraction syntax differs across supported DBs; the admin catalog
    # is small and revision invalidation must be identical on SQLite/Postgres.
    for rule in db.scalars(select(AIAutomation)):
        if rule.action.get(key) == row_id:
            rule.enabled, rule.enabled_at = False, None
            rule.revision += 1
            rule.updated_at = time.time()


@router.get("/connectors")
def list_connectors(db: DB, user: Manager):
    return [
        connector_view(row) for row in db.scalars(select(AIConnector).order_by(AIConnector.name))
    ]


@router.post("/connectors", status_code=201)
def create_connector(value: S.ConnectorIn, db: DB, settings: Settings, user: Manager):
    connectors.validate_url(value.url)
    data = value.model_dump(exclude={"token"})
    row = AIConnector(**data, encrypted_token=encrypted_token(value.token, settings))
    db.add(row)
    db.commit()
    db.refresh(row)
    policy.changed(db, user, "connector_created", row.id)
    return connector_view(row)


@router.patch("/connectors/{row_id}")
def update_connector(
    row_id: str, value: S.ConnectorUpdate, db: DB, settings: Settings, user: Manager
):
    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIConnector, row_id)
        data = value.model_dump(exclude_none=True, exclude={"revision", "token"})
        if value.url is not None:
            connectors.validate_url(value.url)
        if value.token is not None:
            data["encrypted_token"] = encrypted_token(value.token, settings)
        # Changing credentials/destination must not silently alter a live rule.
        if any(key != "enabled" for key in data) or data.get("enabled") is False:
            disable_dependencies(db, "connector_id", row.id)
        row = policy.cas(db, row, value.revision, data)
        result = connector_view(row)
    policy.changed(db, user, "connector_updated", row_id)
    return result


@router.delete("/connectors/{row_id}")
def delete_connector(row_id: str, db: DB, user: Manager):
    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIConnector, row_id)
        disable_dependencies(db, "connector_id", row.id)
        db.delete(row)
        db.commit()
    policy.changed(db, user, "connector_deleted", row_id)
    return {"deleted": True}


@router.get("/scripts")
def list_scripts(db: DB, user: Manager):
    return [
        policy.public_columns(row) for row in db.scalars(select(AIScript).order_by(AIScript.name))
    ]


@router.post("/scripts", status_code=201)
def create_script(value: S.ScriptIn, db: DB, user: Manager):
    automations.validate_script(value.source)
    row = AIScript(**value.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    policy.changed(db, user, "script_created", row.id)
    return policy.public_columns(row)


@router.patch("/scripts/{row_id}")
def update_script(row_id: str, value: S.ScriptUpdate, db: DB, user: Manager):
    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIScript, row_id)
        changes = value.model_dump(exclude_none=True, exclude={"revision"})
        if "source" in changes or "name" in changes:
            automations.validate_script(changes.get("source", row.source))
            changes.update(enabled=False, tested_revision=None)
            disable_dependencies(db, "script_id", row.id)
        elif changes.get("enabled") is True:
            if row.tested_revision != row.revision:
                raise HTTPException(409, "ai_script_test_required")
            # Toggling enabled increments CAS revision without changing source.
            changes["tested_revision"] = value.revision + 1
        else:
            if row.tested_revision == row.revision:
                changes["tested_revision"] = value.revision + 1
            disable_dependencies(db, "script_id", row.id)
        row = policy.cas(db, row, value.revision, changes)
        result = policy.public_columns(row)
    policy.changed(db, user, "script_updated", row_id)
    return result


@router.delete("/scripts/{row_id}")
def delete_script(row_id: str, db: DB, user: Manager):
    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIScript, row_id)
        disable_dependencies(db, "script_id", row.id)
        db.delete(row)
        db.commit()
    policy.changed(db, user, "script_deleted", row_id)
    return {"deleted": True}


@router.post("/scripts/{row_id}/test", status_code=202)
def test_script(row_id: str, value: S.TestIn, db: DB, settings: Settings, user: Manager):
    policy.available(db, settings)
    row = policy.get_row(db, AIScript, row_id)
    automations.bounded_json(value.input)
    return jobs.view(
        jobs.enqueue(
            db,
            user,
            kind="script_test",
            payload={"script_id": row.id, "revision": row.revision, "input": value.input},
        )
    )


@router.post("/drafts", status_code=202)
def create_draft(value: S.DraftIn, db: DB, settings: Settings, user: Manager):
    policy.available(db, settings, ready=True)
    policy.park(db, user, value.park_id)
    payload = value.model_dump()
    payload["instruction"] = knowledge.redact(value.instruction)
    return jobs.view(jobs.enqueue(db, user, kind="draft", park_id=value.park_id, payload=payload))


@router.get("/automations")
def list_automations(db: DB, user: Manager):
    return [
        policy.public_columns(row, ("owner_id", "enabled_at"))
        for row in db.scalars(
            select(AIAutomation)
            .where(AIAutomation.park_id.in_(policy.parks(db, user)))
            .order_by(AIAutomation.name)
        )
    ]


@router.post("/automations", status_code=201)
def create_automation(value: S.AutomationIn, db: DB, user: Manager):
    policy.park(db, user, value.park_id)
    with database_idempotency_lock(db, "ai-controls"):
        data = value.model_dump()
        automations.validate_action(db, data["action"])
        row = AIAutomation(**data, owner_id=user.id)
        db.add(row)
        db.commit()
        db.refresh(row)
        result = policy.public_columns(row, ("owner_id", "enabled_at"))
    policy.changed(db, user, "automation_created", row.id)
    return result


@router.patch("/automations/{row_id}")
def update_automation(row_id: str, value: S.AutomationUpdate, db: DB, user: Manager):
    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIAutomation, row_id)
        policy.park(db, user, row.park_id)
        changes = value.model_dump(exclude_none=True, exclude={"revision"})
        if any(key != "enabled" for key in changes):
            changes.update(enabled=False, enabled_at=None)
        elif changes.get("enabled"):
            changes["enabled_at"] = time.time()
        elif "enabled" in changes:
            changes["enabled_at"] = None
        else:
            raise HTTPException(422, "ai_update_empty")
        automations.validate_action(
            db, changes.get("action", row.action), enabled=changes.get("enabled", False)
        )
        changes["owner_id"] = user.id
        row = policy.cas(db, row, value.revision, changes)
        result = policy.public_columns(row, ("owner_id", "enabled_at"))
    policy.changed(db, user, "automation_updated", row_id)
    return result


@router.delete("/automations/{row_id}")
def delete_automation(row_id: str, db: DB, user: Manager):
    with database_idempotency_lock(db, "ai-controls"):
        row = policy.get_row(db, AIAutomation, row_id)
        policy.park(db, user, row.park_id)
        db.delete(row)
        db.commit()
    policy.changed(db, user, "automation_deleted", row_id)
    return {"deleted": True}


@router.post("/automations/{row_id}/preview")
def preview_automation(row_id: str, value: S.PreviewIn, db: DB, user: Manager):
    row = policy.get_row(db, AIAutomation, row_id)
    policy.park(db, user, row.park_id)
    event = automations.bounded_json(value.event)
    return {
        "matches": automations.matches(row.filters, event),
        "payload": automations.bounded_json(automations.render(row.action.get("body", {}), event)),
    }


@router.get("/runs")
def list_runs(db: DB, user: Manager, limit: int = Query(default=50, ge=1, le=200)):
    return [
        policy.public_columns(row)
        for row in db.scalars(select(AIRun).order_by(AIRun.created_at.desc()).limit(limit))
    ]


@router.delete("/runs")
def purge_runs(db: DB, user: Manager, before_days: int = Query(default=30, ge=1, le=3650)):
    # Keep a minimal delivery receipt for deduplication; only payload/error data
    # is cleared. A cleanup must never cause an external write to be replayed.
    cutoff = time.time() - before_days * 86400
    with database_idempotency_lock(db, "ai-controls"):
        count = db.execute(
            update(AIRun)
            .where(
                AIRun.created_at < cutoff,
                AIRun.state.not_in(("queued", "running", "purged")),
            )
            .values(result=None, error=None, state="purged")
        ).rowcount
        events_cleaned = learning.purge_event_payloads(db, before=cutoff, limit=1000)
        db.commit()
    policy.changed(db, user, "runs_cleaned", "history")
    return {"deleted": count, "events_cleaned": events_cleaned}


@router.post("/maintenance")
def maintenance(value: S.MaintenanceIn, db: DB, user: Manager):
    cutoff = time.time() - value.before_days * 86400
    with database_idempotency_lock(db, "ai-controls"):
        if value.kind == "failed_jobs":
            count = db.execute(
                delete(AIJob).where(
                    AIJob.state.in_(("failed", "cancelled")), AIJob.updated_at < cutoff
                )
            ).rowcount
        else:
            ids = list(
                db.scalars(
                    select(AIConversation.id).where(
                        AIConversation.updated_at < cutoff,
                        ~AIConversation.id.in_(
                            select(AIJob.conversation_id).where(
                                AIJob.state.in_(("queued", "running")),
                                AIJob.conversation_id.is_not(None),
                            )
                        ),
                    )
                )
            )
            db.execute(delete(AIMessage).where(AIMessage.conversation_id.in_(ids)))
            db.execute(delete(AIJob).where(AIJob.conversation_id.in_(ids)))
            count = db.execute(delete(AIConversation).where(AIConversation.id.in_(ids))).rowcount
        db.commit()
    policy.changed(db, user, "history_cleaned", value.kind)
    return {"deleted": count}
