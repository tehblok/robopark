"""Admin and royal triage of unknown samples and live raw-error suppression."""

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_builtin_admin_or_royal
from robopark_api.models import DiagnosticRule, DiagnosticUnknown, User
from robopark_api.routers.admin_diagnostic_rules import (
    RuleId,
    _canonicalize_rule,
    _catalog,
    _record,
    _set_catalog_headers,
    _validate_rule,
    _write,
    require_rule_admin,
)
from robopark_api.schemas import DiagnosticRuleCreate, DiagnosticRuleOut
from robopark_api.services import audit, emergency_cache
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.diagnostic_rules import (
    diagnostic_rule_matches_sample,
    match_diagnostic_events_for_rules,
)
from robopark_api.services.diagnostic_unknowns import canonical, sample_payload

router = APIRouter(
    prefix="/admin/diagnostic-unknowns",
    tags=["admin-diagnostic-unknowns"],
    dependencies=[Depends(require_rule_admin)],
)
State = Literal["new", "mapped", "ignored"]


class UnknownOut(BaseModel):
    id: int
    source_path: str
    raw_value: JsonValue
    original_value: JsonValue
    pattern: str
    first_seen_at: datetime
    last_seen_at: datetime
    observations: int
    last_robot: str
    state: State
    rule_id: int | None


class UnknownPage(BaseModel):
    items: list[UnknownOut]
    total: int
    limit: int
    offset: int
    has_more: bool


class UnknownStateOut(BaseModel):
    id: int
    state: State


class UnknownClassify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: DiagnosticRuleCreate


def _out(row: DiagnosticUnknown) -> UnknownOut:
    raw = json.loads(row.raw_json)
    original = json.loads(row.original_json) if row.original_json is not None else None
    pattern_value = original if original is not None else raw
    return UnknownOut(
        id=row.id,
        source_path=row.source_path,
        raw_value=raw,
        original_value=original,
        pattern=pattern_value if isinstance(pattern_value, str) else canonical(pattern_value),
        first_seen_at=row.first_seen_at.replace(tzinfo=UTC),
        last_seen_at=row.last_seen_at.replace(tzinfo=UTC),
        observations=row.observations,
        last_robot=row.last_robot,
        state=row.state,
        rule_id=row.rule_id,
    )


def _get(db: Session, unknown_id: int, *, lock: bool = False) -> DiagnosticUnknown:
    if lock:
        db.execute(
            update(DiagnosticUnknown)
            .where(DiagnosticUnknown.id == unknown_id)
            .values(observations=DiagnosticUnknown.observations)
            .execution_options(synchronize_session=False)
        )
    row = db.get(DiagnosticUnknown, unknown_id, populate_existing=True)
    if row is None:
        raise HTTPException(status_code=404, detail="diagnostic_unknown_not_found")
    return row


def _audit_unknown(
    db: Session, actor: User, action: str, unknown_id: int, rule_id: int | None = None
) -> None:
    audit.record(
        db,
        actor=actor,
        action=f"admin.diagnostic_unknown.{action}",
        target_type="diagnostic_unknown",
        target_id=str(unknown_id),
        detail=json.dumps({"rule_id": rule_id}),
    )


@router.get("", response_model=UnknownPage)
def list_unknowns(
    response: Response,
    state: State = "new",
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=2**31 - 1),
    db: Session = Depends(get_db),
) -> UnknownPage:
    response.headers["Cache-Control"] = "no-store"
    condition = DiagnosticUnknown.state == state
    total = db.scalar(select(func.count()).select_from(DiagnosticUnknown).where(condition)) or 0
    rows = db.scalars(
        select(DiagnosticUnknown)
        .where(condition)
        .order_by(DiagnosticUnknown.last_seen_at.desc(), DiagnosticUnknown.id.desc())
        .limit(limit)
        .offset(offset)
        .execution_options(populate_existing=True)
    )
    items = [_out(row) for row in rows]
    return UnknownPage(
        items=items, total=total, limit=limit, offset=offset, has_more=offset + len(items) < total
    )


@router.get("/{unknown_id}", response_model=UnknownOut)
def get_unknown(
    unknown_id: RuleId, response: Response, db: Session = Depends(get_db)
) -> UnknownOut:
    response.headers["Cache-Control"] = "no-store"
    return _out(_get(db, unknown_id))


@router.post("/{unknown_id}/classify", response_model=DiagnosticRuleOut, status_code=201)
def classify_unknown(
    unknown_id: RuleId,
    payload: UnknownClassify,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_builtin_admin_or_royal),
) -> DiagnosticRuleOut:
    _canonicalize_rule(payload.rule)
    _validate_rule(payload.rule)
    with _write(db):
        row = _get(db, unknown_id, lock=True)
        if row.state == "mapped":
            raise HTTPException(status_code=409, detail="diagnostic_unknown_already_mapped")
        if row.original_json is None:
            raise HTTPException(status_code=422, detail="unknown_sample_requires_observation")
        candidate = DiagnosticRule(id=0, **payload.rule.model_dump())
        try:
            sample = sample_payload(row)
        except ValueError:
            raise HTTPException(status_code=422, detail="unknown_rule_does_not_match") from None
        if not diagnostic_rule_matches_sample(
            candidate, sample, tuple(json.loads(row.source_segments_json))
        ):
            raise HTTPException(status_code=422, detail="unknown_rule_does_not_match")
        # A match on a previously classified sibling must not mark the residual
        # unknown as mapped. The retained unit must have no raw faults left
        # after applying the current catalog and the candidate together.
        existing = list(db.scalars(select(DiagnosticRule)))
        if any(
            event.rule_id is None
            for event in match_diagnostic_events_for_rules([*existing, candidate], sample)
        ):
            raise HTTPException(status_code=422, detail="unknown_rule_does_not_match")
        rule = DiagnosticRule(**payload.rule.model_dump())
        db.add(rule)
        db.flush()
        row.state = "mapped"
        row.rule_id = rule.id
        db.flush()
        result = DiagnosticRuleOut.model_validate(rule)
        _set_catalog_headers(response, _catalog(db))
    _record(
        db,
        actor,
        audit.ACTION_DIAGNOSTIC_RULE_CREATED,
        result.id,
        fields=sorted(payload.rule.model_dump()),
        sort_order=result.sort_order,
        is_enabled=result.is_enabled,
    )
    _audit_unknown(db, actor, "classified", unknown_id, result.id)
    return result


def _change_state(
    unknown_id: int, state: State, response: Response, db: Session, actor: User
) -> UnknownStateOut:
    vin_to_invalidate: str | None = None
    with _write(db):
        row = _get(db, unknown_id, lock=True)
        if row.state == "mapped":
            raise HTTPException(status_code=409, detail="diagnostic_unknown_already_mapped")
        transitioning = row.state != state
        row.state = state
        row.rule_id = None
        db.flush()
        result = UnknownStateOut(id=row.id, state=row.state)
        if transitioning:
            vin_to_invalidate = row.last_robot
    response.headers["Cache-Control"] = "no-store"
    if vin_to_invalidate:
        emergency_cache.invalidate_vin(
            vin_to_invalidate,
            identity=settings_svc.get_emergency_cookie_identity(db),
        )
    _audit_unknown(db, actor, "ignored" if state == "ignored" else "reopened", unknown_id)
    return result


@router.post("/{unknown_id}/ignore", response_model=UnknownStateOut)
def ignore_unknown(
    unknown_id: RuleId,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_builtin_admin_or_royal),
) -> UnknownStateOut:
    return _change_state(unknown_id, "ignored", response, db, actor)


@router.post("/{unknown_id}/reopen", response_model=UnknownStateOut)
def reopen_unknown(
    unknown_id: RuleId,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_builtin_admin_or_royal),
) -> UnknownStateOut:
    return _change_state(unknown_id, "new", response, db, actor)
