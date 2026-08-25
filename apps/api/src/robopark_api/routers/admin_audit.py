"""Admin-only access to the audit trail."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_admin
from robopark_api.schemas import AuditEntryOut, AuditPageOut
from robopark_api.services import audit

router = APIRouter(
    prefix="/admin/audit",
    tags=["admin-audit"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=AuditPageOut)
def list_audit(
    action: str | None = Query(default=None),
    actor_user_id: int | None = Query(default=None),
    target_id: str | None = Query(default=None),
    park_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> AuditPageOut:
    rows, total = audit.list_entries(
        db,
        action=action,
        actor_user_id=actor_user_id,
        target_id=target_id,
        park_id=park_id,
        limit=limit,
        offset=offset,
    )
    return AuditPageOut(
        items=[AuditEntryOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
    )


@router.get("/actions", response_model=list[str])
def list_actions() -> list[str]:
    """Known action names, for building filters in the UI."""
    return sorted(
        value
        for name, value in vars(audit).items()
        if name.startswith("ACTION_") and isinstance(value, str)
    )
