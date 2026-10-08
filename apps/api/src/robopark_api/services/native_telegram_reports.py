"""Bounded legacy report timing derived from persisted Tracker evidence."""

from __future__ import annotations

import re
import time
from concurrent.futures import FIRST_COMPLETED, wait
from datetime import UTC, datetime
from typing import Literal, TypedDict
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from robopark_api.models import Park
from robopark_api.services import sla_clock, tracker_client, tracker_history
from robopark_api.services.tracker_policy import issue_tags

MAX_REPORT_ISSUES = 500
SLA_AT_RISK_HOURS = 3.0

_LOG_DUMP_EXACT_MARKERS = frozenset({"log_dump", "log-dump", "log dump", "слив логов"})
_LOG_DUMP_SUMMARY = re.compile(
    r"(?<![\w])log_dump(?![\w])|\bdisk\s+space\s+by\b",
    flags=re.IGNORECASE,
)


class BotReportFields(TypedDict):
    repair_hours: float | None
    downtime_hours: float | None
    repair_source: Literal["queued_history"] | None
    sla_hours: float | None
    sla_overdue: bool | None
    sla_at_risk: bool | None
    log_dump: bool
    log_dump_source: Literal["tag", "type", "summary"] | None


class BotReportSummary(TypedDict):
    sla_target_hours: int
    sla_at_risk_hours: float
    sla_evaluated: int
    sla_unknown: int
    sla_at_risk: int
    sla_overdue: int
    log_dump: int


class EnrichedReport(TypedDict):
    issues: list[dict]
    summary: BotReportSummary


def _text(value: object) -> str:
    return str(value or "").strip()


def _reference_values(value: object) -> set[str]:
    if isinstance(value, dict):
        return {
            _text(value.get(key)).casefold()
            for key in ("id", "key", "display", "name")
            if _text(value.get(key))
        }
    text = _text(value)
    return {text.casefold()} if text else set()


def _tag_values(value: object) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return _reference_values(value)
    result: set[str] = set()
    for item in value:
        result.update(_reference_values(item))
    return result


def log_dump_source(issue: dict) -> Literal["tag", "type", "summary"] | None:
    """Classify only explicit legacy phrases or exact Tracker markers."""
    if _tag_values(issue.get("tags")) & _LOG_DUMP_EXACT_MARKERS:
        return "tag"
    if _reference_values(issue.get("type")) & _LOG_DUMP_EXACT_MARKERS:
        return "type"
    if _LOG_DUMP_SUMMARY.search(_text(issue.get("summary"))):
        return "summary"
    return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _timestamp(value: object) -> datetime | None:
    text = _text(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _status_key(issue: dict) -> str:
    value = issue.get("status")
    if isinstance(value, dict):
        return _text(value.get("key") or value.get("id")).casefold()
    return _text(value).casefold()


def _working_hours(start: datetime, end: datetime, *, timezone: str) -> float | None:
    try:
        return sla_clock.elapsed_working_hours(_utc(start), _utc(end), timezone=timezone)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return None


def prepare_history(
    db: Session,
    *,
    token: str,
    park: Park,
    issues: list[dict],
    budget_seconds: float = 8.0,
    max_starts: int = 50,
) -> None:
    """Reuse the bounded shared history pool/cache; never create a report executor.

    Timed-out reads keep filling the shared versioned cache. Unknown evidence
    remains explicit in this report and can be used on the next report.
    """
    attached = tracker_history.attach_verified_history(
        db, [dict(issue) for issue in issues[:MAX_REPORT_ISSUES]]
    )
    remaining = [
        issue
        for issue in attached
        if _status_key(issue)
        and not (
            issue.get("sla_source") == "status_history"
            and _timestamp(issue.get("queued_at")) is not None
            and _text(issue.get("sla_anchor_timezone"))
        )
        and str(issue.get("key") or "").partition("-")[0] == park.tracker_queue
    ]
    deadline = time.monotonic() + min(max(budget_seconds, 0), 8.0)
    starts = 0
    pending = {}
    while (remaining or pending) and time.monotonic() < deadline:
        deferred = []
        for issue in remaining:
            future, started = tracker_client.schedule_issue_status_history(
                token=token,
                key=str(issue["key"]),
                issue=issue,
                allow_start=starts < min(max_starts, 50),
            )
            starts += int(started)
            if future is None:
                deferred.append(issue)
            else:
                pending[future] = issue
        remaining = deferred
        if not pending:
            break
        done, _ = wait(
            pending, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED
        )
        if not done:
            break
        for future in done:
            issue = pending.pop(future)
            history = future.result()
            if history:
                tracker_history.ingest_status_history(
                    db,
                    issue_key=str(issue["key"]),
                    park=park,
                    history=history,
                    current_tags=issue_tags(issue),
                )


def enrich(
    db: Session,
    issues: list[dict],
    *,
    timezone: str,
    now: datetime | None = None,
) -> EnrichedReport:
    """Attach original PNG timers from the shared first-queue SLA ledger."""
    observed_at = _utc(now or datetime.now(UTC))
    scoped = tracker_history.attach_verified_history(
        db, [dict(issue) for issue in issues[:MAX_REPORT_ISSUES]]
    )

    counters = {
        "sla_evaluated": 0,
        "sla_unknown": 0,
        "sla_at_risk": 0,
        "sla_overdue": 0,
        "log_dump": 0,
    }
    enriched: list[dict] = []
    for issue in scoped:
        created = _timestamp(issue.get("createdAt"))
        downtime = (
            (observed_at - created).total_seconds() / 3600
            if created is not None and created <= observed_at
            else None
        )
        repair_hours = None
        repair_source: Literal["queued_history"] | None = None
        anchor_timezone = _text(issue.get("sla_anchor_timezone"))
        sla = tracker_client.repair_sla_fields(issue, timezone=anchor_timezone or None)
        queued_at = _timestamp(sla.get("queued_at"))
        deadline = _timestamp(sla.get("sla_deadline"))
        if queued_at is not None and queued_at <= observed_at and anchor_timezone:
            repair_hours = _working_hours(queued_at, observed_at, timezone=anchor_timezone)
            repair_source = "queued_history" if repair_hours is not None else None

        source = log_dump_source(issue)
        if source is not None:
            counters["log_dump"] += 1
            overdue = at_risk = None
        elif repair_hours is None:
            counters["sla_unknown"] += 1
            overdue = at_risk = None
        else:
            overdue = deadline is not None and observed_at > deadline
            at_risk = not overdue and repair_hours >= SLA_AT_RISK_HOURS
            counters["sla_evaluated"] += 1
            counters["sla_overdue"] += int(overdue)
            counters["sla_at_risk"] += int(at_risk)
        fields: BotReportFields = {
            "repair_hours": repair_hours,
            "downtime_hours": downtime,
            "repair_source": repair_source,
            "sla_hours": None if source is not None else repair_hours,
            "sla_overdue": overdue,
            "sla_at_risk": at_risk,
            "log_dump": source is not None,
            "log_dump_source": source,
        }
        enriched.append({**issue, "bot_report": fields})
    return {
        "issues": enriched,
        "summary": {
            "sla_target_hours": sla_clock.SLA_TARGET_HOURS,
            "sla_at_risk_hours": SLA_AT_RISK_HOURS,
            **counters,
        },
    }
