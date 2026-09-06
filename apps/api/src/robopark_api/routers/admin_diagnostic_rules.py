"""Global diagnostic catalog. Preview is pure; writes retain structural audit only.

GET and mutation responses include a catalog ETag. PUT /reorder requires that
exact ETag in If-Match and every ID, including disabled rules, exactly once.
Missing preconditions return 428, stale catalogs/uniqueness conflicts 409, and
invalid input 422. Ordinary create/update/disable do not require a precondition.
PATCH rejects sort_order; changing an existing rule's order requires reorder.
"""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import DiagnosticRule, User
from robopark_api.schemas import (
    DiagnosticEvent,
    DiagnosticRuleCreate,
    DiagnosticRuleOut,
    DiagnosticRuleUpdate,
)
from robopark_api.services import audit, rbac
from robopark_api.services.diagnostic_rules import (
    compile_diagnostic_regex,
    diagnostic_source_parts,
    match_diagnostic_events_for_rules,
)


def require_rule_admin(user: User = Depends(require_user)) -> User:
    rbac.assert_approved(user)
    if not rbac.is_admin_or_royal(user):
        raise HTTPException(status_code=403)
    return user


router = APIRouter(
    prefix="/admin/diagnostic-rules",
    tags=["admin-diagnostic-rules"],
    dependencies=[Depends(require_rule_admin)],
)

RuleId = Annotated[int, Path(gt=0, le=2**63 - 1)]


class DiagnosticRulesReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[Annotated[int, Field(strict=True, gt=0)]]


class DiagnosticRulePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule: DiagnosticRuleCreate
    payload: dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "Optional full Emergency-shaped payload. When absent, rule.example is parsed "
            "as JSON if valid, otherwise used as plain text at rule.source_path. "
            "Numeric non-root path segments construct arrays, with a 10000-slot total limit."
        ),
    )


class DiagnosticRulePreviewOut(BaseModel):
    matched: bool
    events: list[DiagnosticEvent]


def validation_error_details(errors: list[Any]) -> list[dict[str, Any]]:
    """Keep validation guidance, excluding input, decoder context and user-supplied keys."""
    known_locations = set(DiagnosticRuleCreate.model_fields) | {
        "body",
        "path",
        "query",
        "header",
        "rule",
        "payload",
        "ids",
        "rule_id",
        "if-match",
    }
    return [
        {
            "loc": [
                part if isinstance(part, int) or part in known_locations else "[redacted]"
                for part in error["loc"]
            ],
            "msg": error["msg"],
            "type": error["type"],
        }
        for error in errors
    ]


def _validate_rule(rule: DiagnosticRuleCreate) -> tuple[str, ...]:
    if not -(2**63) <= rule.sort_order < 2**63:
        raise HTTPException(status_code=422, detail="invalid_diagnostic_sort_order")
    parts = diagnostic_source_parts(rule.source_path)
    if parts is None:
        raise HTTPException(status_code=422, detail="invalid_diagnostic_source_path")
    if rule.match_kind == "regex":
        try:
            compile_diagnostic_regex(rule.pattern)
        except ValueError as exc:
            # The shared compiler exposes only fixed error codes, never the pattern.
            raise HTTPException(status_code=422, detail=str(exc)) from None
    return parts


def _catalog(db: Session) -> list[DiagnosticRuleOut]:
    return [
        DiagnosticRuleOut.model_validate(rule)
        for rule in db.scalars(
            select(DiagnosticRule)
            .order_by(DiagnosticRule.sort_order, DiagnosticRule.id)
            .execution_options(populate_existing=True)
        )
    ]


def _etag(rules: list[DiagnosticRuleOut]) -> str:
    serialized = json.dumps(
        [rule.model_dump() for rule in rules],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return '"' + hashlib.sha256(serialized.encode()).hexdigest() + '"'


def _set_catalog_headers(response: Response, rules: list[DiagnosticRuleOut]) -> None:
    response.headers["ETag"] = _etag(rules)
    response.headers["Cache-Control"] = "no-store"


@contextmanager
def _write(db: Session) -> Iterator[None]:
    try:
        # Claim the write transaction before reading the catalog. This is the
        # repository's no-op UPDATE locking convention: SQLite serializes even
        # an empty catalog, while row-locking databases lock existing rules.
        # Do not trust a previously loaded identity-map object after this claim.
        db.execute(
            update(DiagnosticRule)
            .values(sort_order=DiagnosticRule.sort_order)
            .execution_options(synchronize_session=False)
        )
        yield
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="diagnostic_rule_conflict") from None
    except OperationalError as exc:
        db.rollback()
        sqlite_code = getattr(exc.orig, "sqlite_errorcode", 0)
        contended = sqlite_code & 0xFF in {5, 6} or getattr(exc.orig, "pgcode", None) in {
            "40001",
            "40P01",
            "55P03",
        }
        # Never propagate database exceptions with bound pattern/example values.
        raise HTTPException(
            status_code=409 if contended else 503,
            detail="diagnostic_rules_write_conflict"
            if contended
            else "diagnostic_rules_unavailable",
        ) from None
    except Exception:
        db.rollback()
        raise


def _get_rule(db: Session, rule_id: int) -> DiagnosticRule:
    rule = db.get(DiagnosticRule, rule_id, populate_existing=True)
    if rule is None:
        raise HTTPException(status_code=404, detail="diagnostic_rule_not_found")
    return rule


def _record(db: Session, actor: User, action: str, rule_id: int, **metadata: Any) -> None:
    # Callers supply field NAMES and numeric/boolean structure only. In
    # particular source paths, free text, patterns and examples stay out.
    audit.record(
        db,
        actor=actor,
        action=action,
        target_type="diagnostic_rule",
        target_id=str(rule_id),
        detail=json.dumps(metadata, sort_keys=True),
    )


@router.get("", response_model=list[DiagnosticRuleOut])
def list_rules(response: Response, db: Session = Depends(get_db)) -> list[DiagnosticRuleOut]:
    rules = _catalog(db)
    _set_catalog_headers(response, rules)
    return rules


@router.post("", response_model=DiagnosticRuleOut, status_code=201)
def create_rule(
    payload: DiagnosticRuleCreate,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_rule_admin),
) -> DiagnosticRuleOut:
    _validate_rule(payload)
    with _write(db):
        rule = DiagnosticRule(**payload.model_dump())
        db.add(rule)
        db.flush()
        result = DiagnosticRuleOut.model_validate(rule)
        _set_catalog_headers(response, _catalog(db))
    _record(
        db,
        actor,
        audit.ACTION_DIAGNOSTIC_RULE_CREATED,
        result.id,
        fields=sorted(payload.model_dump()),
        sort_order=result.sort_order,
        is_enabled=result.is_enabled,
    )
    return result


@router.put("/reorder", response_model=list[DiagnosticRuleOut])
def reorder_rules(
    payload: DiagnosticRulesReorder,
    response: Response,
    if_match: str | None = Header(default=None),
    db: Session = Depends(get_db),
    actor: User = Depends(require_rule_admin),
) -> list[DiagnosticRuleOut]:
    if if_match is None:
        raise HTTPException(status_code=428, detail="diagnostic_rules_precondition_required")
    with _write(db):
        before = _catalog(db)
        if if_match != _etag(before):
            raise HTTPException(status_code=409, detail="diagnostic_rules_changed")
        if len(set(payload.ids)) != len(payload.ids) or set(payload.ids) != {
            rule.id for rule in before
        }:
            raise HTTPException(status_code=422, detail="ids_must_include_all_rules_once")
        for sort_order, rule_id in enumerate(payload.ids):
            _get_rule(db, rule_id).sort_order = sort_order
        db.flush()
        result = _catalog(db)
        _set_catalog_headers(response, result)
    previous = {rule.id: rule.sort_order for rule in before}
    for rule in result:
        _record(
            db,
            actor,
            audit.ACTION_DIAGNOSTIC_RULE_REORDERED,
            rule.id,
            previous_sort_order=previous[rule.id],
            sort_order=rule.sort_order,
        )
    return result


def _example_payload(example: str, parts: tuple[str, ...]) -> dict[str, Any]:
    try:
        value = json.loads(example)
    except (ValueError, RecursionError):
        value = example
    # The root is always a JSON object (including numeric dictionary keys).
    # Remaining numeric segments use arrays, matching the live lookup syntax.
    slots = 0
    for part in reversed(parts[1:]):
        if part.isascii() and part.isdecimal():
            index = int(part)
            slots += index + 1
            if slots > 10000:
                raise HTTPException(status_code=422, detail="diagnostic_preview_source_too_large")
            value = [None] * index + [value]
        else:
            value = {part: value}
    return {parts[0]: value}


@router.post("/preview", response_model=DiagnosticRulePreviewOut)
def preview_rule(payload: DiagnosticRulePreview, response: Response) -> DiagnosticRulePreviewOut:
    parts = _validate_rule(payload.rule)
    example = (
        payload.payload
        if payload.payload is not None
        else _example_payload(payload.rule.example, parts)
    )
    # ID 0 marks this unsaved candidate. The object is never added to a Session.
    candidate = DiagnosticRule(id=0, **payload.rule.model_dump())
    events = match_diagnostic_events_for_rules([candidate], example)
    response.headers["Cache-Control"] = "no-store"
    return DiagnosticRulePreviewOut(
        matched=any(event.rule_id == 0 for event in events), events=events
    )


@router.patch("/{rule_id}", response_model=DiagnosticRuleOut)
def update_rule(
    rule_id: RuleId,
    payload: DiagnosticRuleUpdate,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_rule_admin),
) -> DiagnosticRuleOut:
    changes = payload.model_dump(exclude_unset=True)
    with _write(db):
        rule = _get_rule(db, rule_id)
        values = {key: getattr(rule, key) for key in DiagnosticRuleCreate.model_fields}
        try:
            candidate = DiagnosticRuleCreate.model_validate({**values, **changes})
        except ValidationError:
            raise HTTPException(status_code=422, detail="invalid_diagnostic_rule") from None
        _validate_rule(candidate)
        changed_fields = sorted(key for key, value in changes.items() if values[key] != value)
        for key in changed_fields:
            setattr(rule, key, changes[key])
        db.flush()
        result = DiagnosticRuleOut.model_validate(rule)
        _set_catalog_headers(response, _catalog(db))
    if changed_fields:
        _record(db, actor, audit.ACTION_DIAGNOSTIC_RULE_UPDATED, result.id, fields=changed_fields)
    return result


@router.post("/{rule_id}/disable", response_model=DiagnosticRuleOut)
def disable_rule(
    rule_id: RuleId,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_rule_admin),
) -> DiagnosticRuleOut:
    with _write(db):
        rule = _get_rule(db, rule_id)
        changed = rule.is_enabled
        rule.is_enabled = False
        db.flush()
        result = DiagnosticRuleOut.model_validate(rule)
        _set_catalog_headers(response, _catalog(db))
    if changed:
        _record(db, actor, audit.ACTION_DIAGNOSTIC_RULE_DISABLED, rule_id, is_enabled=False)
    return result
