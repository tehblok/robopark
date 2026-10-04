"""Authority comes from the host and RBAC, never from an LLM or imported document."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update

from robopark_api.ai_models import AIConfig
from robopark_api.models import Park, UserPark
from robopark_api.services import audit, rbac

STAFF = {"mechanic", "operator", "admin", "royal"}


def staff(db, user):
    if not user or not user.is_active or user.access_status != "approved" or user.role not in STAFF:
        raise HTTPException(403, "ai_forbidden")
    if not rbac.has_permission(db, user, "tracker.read"):
        raise HTTPException(403, "ai_forbidden")


def can_manage(db, user):
    return bool(
        user
        and user.is_active
        and user.access_status == "approved"
        and user.role in {"admin", "royal"}
        and rbac.has_permission(db, user, "nav.admin")
    )


def manager(db, user):
    staff(db, user)
    if not can_manage(db, user):
        raise HTTPException(403, "ai_manage_forbidden")


def parks(db, user):
    staff(db, user)
    query = select(Park.id).where(Park.is_active.is_(True))
    if user.role not in {"admin", "royal"}:
        query = query.join(UserPark).where(UserPark.user_id == user.id)
    return set(db.scalars(query))


def park(db, user, park_id, *, global_allowed=False):
    if park_id is None and global_allowed:
        staff(db, user)
        return
    if park_id not in parks(db, user):
        raise HTTPException(403, "ai_park_forbidden")


def host_status(settings):
    unavailable = {
        "schema": 1,
        "supported": False,
        "installed": False,
        "enabled": False,
        "ready": False,
        "reason": "agx_required",
        "model": "Ternary-Bonsai-2-27B-PQ2_0",
        "backend": None,
    }
    try:
        with Path(settings.ai_runtime_state_path).open("rb") as handle:
            raw = handle.read(8193)
        value = json.loads(raw) if len(raw) <= 8192 else None
        if (
            not isinstance(value, dict)
            or value.get("schema") != 1
            or value.get("supported") is not True
        ):
            return unavailable
        if any(not isinstance(value.get(k), bool) for k in ("installed", "enabled", "ready")):
            return unavailable
        return {
            **unavailable,
            **{k: value[k] for k in unavailable if k in value},
            "supported": True,
        }
    except (OSError, ValueError, TypeError):
        return unavailable


def hardware(settings):
    state = host_status(settings)
    if not state["supported"]:
        raise HTTPException(409, "ai_agx_required")
    return state


def config(db):
    row = db.get(AIConfig, 1)
    return (
        {"enabled": row.enabled, "learning_enabled": row.learning_enabled, "revision": row.revision}
        if row
        else {"enabled": True, "learning_enabled": True, "revision": 1}
    )


def available(db, settings, *, ready=False):
    state = hardware(settings)
    if not config(db)["enabled"] or not state["enabled"]:
        raise HTTPException(409, "ai_disabled")
    if ready and (not state["ready"] or state["backend"] != "cuda"):
        raise HTTPException(503, "ai_not_ready")
    return state


def changed(db, user, kind, target_id):
    audit.record(db, action=f"ai.{kind}", actor=user, target_type="ai", target_id=str(target_id))


def cas(db, row, revision, changes):
    """Update only the revision the administrator actually reviewed."""
    values = {**changes, "revision": revision + 1}
    if hasattr(row, "updated_at"):
        values["updated_at"] = time.time()
    identity = "id" if hasattr(row, "id") else "role"
    statement = (
        update(type(row))
        .where(
            getattr(type(row), identity) == getattr(row, identity), type(row).revision == revision
        )
        .values(**values)
    )
    if db.execute(statement.execution_options(synchronize_session=False)).rowcount != 1:
        db.rollback()
        raise HTTPException(409, "ai_revision_conflict")
    db.commit()
    db.refresh(row)
    return row


def get_row(db, model, row_id):
    row = db.get(model, row_id)
    if row is None:
        raise HTTPException(404, "ai_not_found")
    return row


def columns(row, exclude=()):
    return {
        col.name: getattr(row, col.name) for col in row.__table__.columns if col.name not in exclude
    }


def public_columns(row, exclude=()):
    value = columns(row, exclude)
    for key in ("created_at", "updated_at", "enabled_at"):
        if key in value and value[key] is not None:
            value[key] = datetime.fromtimestamp(value[key], UTC).isoformat()
    return value
