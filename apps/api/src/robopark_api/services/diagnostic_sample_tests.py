"""Private, bounded evaluation of drafts against stored original diagnostic units."""

import json
import math
import time
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from robopark_api.models import DiagnosticRule, DiagnosticUnknown
from robopark_api.services.diagnostic_rules import (
    compile_diagnostic_regex,
    diagnostic_rule_matches_sample,
    diagnostic_source_parts,
)
from robopark_api.services.diagnostic_unknowns import _SENSITIVE, MAX_SAMPLE_BYTES, _sensitive

MAX_MATCH_OPERATIONS = 2000
MAX_SECONDS = 1.0
MAX_CATALOG_RULES = 100


class SampleTestItem(BaseModel):
    id: int
    outcome: Literal["matched", "missed", "skipped"]
    overlap_rule_ids: list[int] = []
    reason: Literal["legacy", "unusable", "budget"] | None = None


class SampleTestResult(BaseModel):
    items: list[SampleTestItem]
    matched: int
    missed: int
    skipped: int
    overlapping: int
    limit: int
    has_more: bool
    budget_exhausted: bool
    invalid_rule_ids: list[int]


class SampleBudgetExceeded(Exception):
    pass


class CatalogTooLarge(Exception):
    pass


def _reconstruct(original_json: str, segments_json: str) -> tuple[dict, tuple]:
    if len(original_json.encode()) > MAX_SAMPLE_BYTES or len(segments_json.encode()) > 2048:
        raise ValueError("unusable")
    value, segments = json.loads(original_json), json.loads(segments_json)
    if (
        type(segments) is not list
        or not 1 <= len(segments) <= 32
        or type(segments[0]) is not str
        or any(type(part) not in (str, int) for part in segments)
        or any(type(part) is int and not 0 <= part < 10000 for part in segments)
        or sum(part + 1 for part in segments if type(part) is int) > 10000
        or any(
            type(part) is str
            and (_SENSITIVE.search(part) or diagnostic_source_parts(part) != (part,))
            for part in segments
        )
    ):
        raise ValueError("unusable")
    pending = [(value, 0)]
    nodes = 0
    while pending:
        node, depth = pending.pop()
        nodes += 1
        if nodes > 256 or depth > 24 or (type(node) is float and not math.isfinite(node)):
            raise ValueError("unusable")
        if type(node) is dict:
            pending.extend((child, depth + 1) for child in node.values())
        elif type(node) is list:
            pending.extend((child, depth + 1) for child in node)
    if _sensitive(value):
        raise ValueError("unusable")
    for part in reversed(segments):
        value = [None] * part + [value] if type(part) is int else {part: value}
    return value, tuple(segments)


def evaluate_samples(
    db: Session, candidate: DiagnosticRule, limit: int, exclude_rule_id: int | None
) -> SampleTestResult:
    # A read operation must not flush unrelated changes from a reused session.
    with db.no_autoflush:
        rules = list(
            db.scalars(
                select(DiagnosticRule)
                .where(DiagnosticRule.is_enabled.is_(True))
                .order_by(DiagnosticRule.id)
                .limit(MAX_CATALOG_RULES + 1)
            )
        )
        if len(rules) > MAX_CATALOG_RULES:
            raise CatalogTooLarge
        # Project only bounded originals and paths; raw residuals/robot identifiers
        # never enter this evaluator. Oversized persisted values fail closed.
        rows = list(
            db.execute(
                select(
                    DiagnosticUnknown.id,
                    case(
                        (
                            func.length(DiagnosticUnknown.original_json) <= MAX_SAMPLE_BYTES,
                            DiagnosticUnknown.original_json,
                        ),
                        else_="",
                    ).label("original_json"),
                    case(
                        (
                            func.length(DiagnosticUnknown.source_segments_json) <= 2048,
                            DiagnosticUnknown.source_segments_json,
                        ),
                        else_="",
                    ).label("source_segments_json"),
                    DiagnosticUnknown.original_json.is_(None).label("legacy"),
                )
                .order_by(DiagnosticUnknown.last_seen_at.desc(), DiagnosticUnknown.id.desc())
                .limit(limit + 1)
            )
        )
    invalid = []
    valid_rules = []
    for rule in rules:
        if rule.id == exclude_rule_id:
            continue
        try:
            if diagnostic_source_parts(rule.source_path) is None:
                raise ValueError("invalid")
            if rule.match_kind == "regex":
                compile_diagnostic_regex(rule.pattern)
        except ValueError:
            invalid.append(rule.id)
        else:
            valid_rules.append(rule)
    deadline, operations = time.monotonic() + MAX_SECONDS, 0
    exhausted = False

    def consume():
        nonlocal operations
        operations += 1
        if operations > MAX_MATCH_OPERATIONS or time.monotonic() >= deadline:
            raise SampleBudgetExceeded

    items = []
    for row in rows[:limit]:
        if row.legacy:
            items.append(SampleTestItem(id=row.id, outcome="skipped", reason="legacy"))
            continue
        try:
            consume()
            payload, location = _reconstruct(row.original_json, row.source_segments_json)
            matched = diagnostic_rule_matches_sample(candidate, payload, location, consume=consume)
            overlaps = [
                rule.id
                for rule in valid_rules
                if matched
                and diagnostic_rule_matches_sample(rule, payload, location, consume=consume)
            ]
            consume()
            items.append(
                SampleTestItem(
                    id=row.id, outcome="matched" if matched else "missed", overlap_rule_ids=overlaps
                )
            )
        except SampleBudgetExceeded:
            exhausted = True
            items.append(SampleTestItem(id=row.id, outcome="skipped", reason="budget"))
        except (ValueError, TypeError, RecursionError, OverflowError):
            items.append(SampleTestItem(id=row.id, outcome="skipped", reason="unusable"))
    return SampleTestResult(
        items=items,
        matched=sum(item.outcome == "matched" for item in items),
        missed=sum(item.outcome == "missed" for item in items),
        skipped=sum(item.outcome == "skipped" for item in items),
        overlapping=sum(bool(item.overlap_rule_ids) for item in items),
        limit=limit,
        has_more=len(rows) > limit,
        budget_exhausted=exhausted,
        invalid_rule_ids=invalid,
    )
