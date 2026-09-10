"""Persist exact bounded diagnostic units, never upstream snapshots.

Counts are sampled sightings (at most once per robot/error every 60 seconds),
not distinct incidents. Matcher identities already ignore expanded array indexes.
Sensitive or oversized units are skipped rather than changing their raw meaning.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from robopark_api.models import DiagnosticUnknown, DiagnosticUnknownSighting
from robopark_api.schemas import DiagnosticEvent
from robopark_api.services.diagnostic_rules import diagnostic_source_parts

logger = logging.getLogger(__name__)
MAX_SAMPLE_BYTES = 8192
SAMPLE_INTERVAL = timedelta(seconds=60)
_SENSITIVE = re.compile(
    r"password|passwd|secret|token|cookie|authorization|api[_-]?key|credential", re.I
)
_CREDENTIAL_VALUE = re.compile(
    r"\b(?:Bearer|Basic)\s+\S+|(?:password|token|secret|cookie|api[_-]?key)\s*[=:]\s*\S+", re.I
)


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def ignored_diagnostic_identities(db: Session) -> set[str]:
    """Return only raw diagnostic identities deliberately hidden by an admin."""
    return set(
        db.scalars(select(DiagnosticUnknown.identity).where(DiagnosticUnknown.state == "ignored"))
    )


def _sensitive(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_SENSITIVE.search(key) or _sensitive(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_sensitive(child) for child in value)
    return isinstance(value, str) and bool(_CREDENTIAL_VALUE.search(value))


def sample_payload(row: DiagnosticUnknown) -> dict:
    """Reconstruct only the sample at its original typed path, preserving scalar strings."""
    value = json.loads(row.original_json)
    segments = json.loads(row.source_segments_json)
    slots = 0
    for part in reversed(segments):
        if type(part) is int:
            slots += part + 1
            if slots > 10000:
                raise ValueError("unknown_sample_path_too_large")
            value = [None] * part + [value]
        else:
            value = {part: value}
    return value


def _capture_unknowns(
    db: Session,
    events: list[DiagnosticEvent],
    robot: str,
    *,
    payload: dict,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    samples = []
    for event in events:
        if event.rule_id is not None:
            continue
        segments = list(event.source_segments)
        suggested = list(segments)
        while suggested and type(suggested[-1]) is int:
            suggested.pop()
        source = ".".join(str(part) for part in suggested)
        raw = canonical(event.raw_value)
        original = payload
        for segment in segments:
            original = original[segment]
        original_json = canonical(original)
        if (
            not suggested
            or diagnostic_source_parts(source) is None
            or any(type(part) is str and ("." in part or "\\" in part) for part in segments)
            or sum(part + 1 for part in segments if type(part) is int) > 10000
            or len(raw.encode()) + len(original_json.encode()) > MAX_SAMPLE_BYTES
            or _sensitive(original)
            or _sensitive(event.raw_value)
            or any(type(part) is str and _SENSITIVE.search(part) for part in segments)
        ):
            continue
        samples.append((event, source, raw, original_json))
    if not samples:
        return
    # Most snapshot polls repeat recent samples: keep those entirely read-only.
    # These scalar reads bypass the identity map. Writers still recheck under
    # the database lock below, so racing/new/expired sightings remain atomic.
    identities = {event.id for event, _, _, _ in samples}
    recent = {
        identity
        for identity, sampled_at in db.execute(
            select(DiagnosticUnknown.identity, DiagnosticUnknownSighting.sampled_at)
            .join(DiagnosticUnknownSighting)
            .where(
                DiagnosticUnknown.identity.in_(identities),
                DiagnosticUnknownSighting.robot == robot,
                DiagnosticUnknown.original_json.is_not(None),
            )
        )
        if now - sampled_at.replace(tzinfo=UTC) < SAMPLE_INTERVAL
    }
    if identities <= recent:
        return
    # Lock before reading. Unique identity and composite sighting keys also
    # protect insertion races on row-locking databases when the table is empty.
    for attempt in range(2):
        try:
            db.execute(
                update(DiagnosticUnknown)
                .where(DiagnosticUnknown.identity.in_([event.id for event, _, _, _ in samples]))
                .values(observations=DiagnosticUnknown.observations)
                .execution_options(synchronize_session=False)
            )
            for event, source, raw, original_json in samples:
                row = db.scalar(
                    select(DiagnosticUnknown)
                    .where(DiagnosticUnknown.identity == event.id)
                    .execution_options(populate_existing=True)
                )
                if row is None:
                    row = DiagnosticUnknown(
                        identity=event.id,
                        source_path=source,
                        source_segments_json=canonical(event.source_segments),
                        raw_json=raw,
                        original_json=original_json,
                        first_seen_at=now,
                        last_seen_at=now,
                        observations=1,
                        last_robot=robot,
                        state="new",
                    )
                    db.add(row)
                    db.flush()
                    db.add(
                        DiagnosticUnknownSighting(unknown_id=row.id, robot=robot, sampled_at=now)
                    )
                else:
                    # Backfill legacy rows only from a real authorized payload.
                    # Refresh the paired typed path with the exact unit: indexes
                    # can move while the matcher identity remains unchanged.
                    row.original_json = original_json
                    row.source_segments_json = canonical(event.source_segments)
                    row.source_path = source
                    sighting = db.get(
                        DiagnosticUnknownSighting, (row.id, robot), populate_existing=True
                    )
                    if (
                        sighting is not None
                        and now - sighting.sampled_at.replace(tzinfo=UTC) < SAMPLE_INTERVAL
                    ):
                        continue
                    if sighting is None:
                        db.add(
                            DiagnosticUnknownSighting(
                                unknown_id=row.id, robot=robot, sampled_at=now
                            )
                        )
                    else:
                        sighting.sampled_at = now
                    row.observations += 1
                    row.last_seen_at = now
                    row.last_robot = robot
            db.commit()
            return
        except IntegrityError:
            db.rollback()
            if attempt == 0:
                continue
            logger.warning("diagnostic_unknown_capture_unavailable")
        except Exception:
            db.rollback()
            # No SQL parameters, raw samples, upstream content or exception text.
            logger.warning("diagnostic_unknown_capture_unavailable")
            return


def capture_unknowns(
    db: Session,
    events: list[DiagnosticEvent],
    robot: str,
    *,
    payload: dict,
    now: datetime | None = None,
) -> None:
    try:
        _capture_unknowns(db, events, robot, payload=payload, now=now)
    except Exception:
        db.rollback()
        logger.warning("diagnostic_unknown_capture_unavailable")
