"""Aggregate measured history without filling missing buckets with zeros."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from statistics import median
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from robopark_api.analytics_schemas import (
    AnalyticsBucket,
    AnalyticsCoverage,
    AnalyticsMetric,
    AnalyticsOut,
    AnalyticsPeriod,
    AnalyticsSeries,
    VerifiedClosureSummary,
)
from robopark_api.models import (
    AnalyticsObservation,
    AnalyticsSnapshot,
    Park,
    ParkBlockerHistory,
    TrackerHistoryBackfillCursor,
    TrackerIssueHistoryState,
    TrackerIssueStatusEvent,
)
from robopark_api.services import sla_clock
from robopark_api.services.blocker_history import align_bucket_start
from robopark_api.services.operations import STATUS_LABELS, as_utc

STEP = timedelta(hours=2)
STAGES = [key for key in STATUS_LABELS if key != "all"]
AGE_BANDS = {"under_24h": (0, 24), "24_to_72h": (24, 72), "over_72h": (72, float("inf"))}
CLOSED_STATUSES = frozenset(
    {"closed", "resolved", "закрыт", "закрыта", "закрыто", "решен", "решена", "решено"}
)


@dataclass
class Sample:
    value: float | None
    keys: tuple[str, ...] = ()
    count: int = 1
    complete: bool = True


def coverage(start: datetime, end: datetime, observed: int, *, complete=True) -> AnalyticsCoverage:
    expected = int((end - start) / STEP)
    return AnalyticsCoverage(
        period=AnalyticsPeriod(start=start, end=end),
        observed_buckets=observed,
        expected_buckets=expected,
        complete=complete and observed == expected,
    )


def metric(
    key, unit, samples, start, end, aggregation, *, include_task_keys=True
) -> AnalyticsMetric:
    measured = [
        sample
        for time, sample in samples.items()
        if start <= time < end and sample.value is not None
    ]
    count = sum(sample.count for sample in measured)
    value = None
    if measured:
        total = sum(sample.value for sample in measured)
        value = (
            total
            if aggregation == "sum"
            else (total / len(measured) if aggregation == "mean" else total * 100 / count)
        )
    task_keys = (
        sorted({key for sample in measured for key in sample.keys}) if include_task_keys else []
    )
    return AnalyticsMetric(
        **coverage(
            start, end, len(measured), complete=all(s.complete for s in measured)
        ).model_dump(),
        key=key,
        unit=unit,
        value=value,
        sample_count=count,
        task_keys=task_keys,
        task_keys_count=len(task_keys),
    )


def series(key, unit, samples, start, end, bucket, aggregation="mean") -> AnalyticsSeries:
    step = STEP if bucket == "2h" else timedelta(days=1)
    points = []
    cursor = start
    while cursor < end:
        points.append(
            metric(
                key,
                unit,
                samples,
                cursor,
                min(cursor + step, end),
                aggregation,
                include_task_keys=False,
            )
        )
        cursor += step
    return AnalyticsSeries(
        **metric(key, unit, samples, start, end, aggregation).model_dump(),
        aggregation=aggregation,
        points=points,
    )


def verified_closures(
    db: Session, *, park_id: int, start: datetime, end: datetime, scoped: bool
) -> VerifiedClosureSummary:
    if scoped:
        return VerifiedClosureSummary(count=None)
    events = db.execute(
        select(
            TrackerIssueHistoryState.issue_key,
            TrackerIssueHistoryState.latest_status_key,
            TrackerIssueHistoryState.first_queued_at,
            TrackerIssueHistoryState.anchor_timezone,
            TrackerIssueHistoryState.terminal_at,
            TrackerIssueStatusEvent.to_status_key,
            TrackerIssueStatusEvent.to_status_display,
        )
        .join(
            TrackerIssueStatusEvent,
            and_(
                TrackerIssueStatusEvent.issue_key == TrackerIssueHistoryState.issue_key,
                TrackerIssueStatusEvent.occurred_at == TrackerIssueHistoryState.terminal_at,
            ),
        )
        .where(
            TrackerIssueStatusEvent.park_id == park_id,
            TrackerIssueHistoryState.terminal_at >= start,
            TrackerIssueHistoryState.terminal_at < end,
        )
    ).all()
    closed: dict[str, tuple[datetime | None, str | None, datetime]] = {}
    for issue_key, latest, first, timezone, terminal, key, display in events:
        if latest not in (key, display):
            continue
        if any(
            str(value or "").strip().lower().replace("ё", "е") in CLOSED_STATUSES
            for value in (key, display)
        ):
            closed[issue_key] = (first, timezone, terminal)
    keys = sorted(closed)
    on_time = late = unknown = 0
    downtimes = []
    for first, timezone, terminal in closed.values():
        if first is None or as_utc(first) > as_utc(terminal):
            unknown += 1
            continue
        downtimes.append((as_utc(terminal) - as_utc(first)).total_seconds() / 3600)
        if not timezone:
            unknown += 1
            continue
        try:
            due_at = sla_clock.deadline(as_utc(first), timezone=timezone)
        except (ValueError, ZoneInfoNotFoundError):
            unknown += 1
            continue
        if as_utc(terminal) <= due_at:
            on_time += 1
        else:
            late += 1
    # Reads are limited per scan and old closed tickets may never have been
    # observed. This is a verified lower bound, not the total closed workload.
    return VerifiedClosureSummary(
        count=len(keys),
        task_keys=keys,
        sla_on_time_count=on_time,
        sla_late_count=late,
        sla_unknown_count=unknown,
        sla_on_time_percent=on_time * 100 / (on_time + late) if on_time + late else None,
        downtime_sample_count=len(downtimes),
        median_downtime_hours=median(downtimes) if downtimes else None,
        p90_downtime_hours=sorted(downtimes)[ceil(0.9 * len(downtimes)) - 1] if downtimes else None,
    )


def build_analytics(
    db: Session,
    *,
    park_id: int,
    days: int,
    bucket: AnalyticsBucket,
    now: datetime | None = None,
    allowed_statuses: set[str] | None = None,
) -> AnalyticsOut:
    now = as_utc(now or datetime.now(UTC))
    end = align_bucket_start(now)
    start = end - timedelta(days=days)
    history = db.scalars(
        select(ParkBlockerHistory).where(
            ParkBlockerHistory.park_id == park_id,
            ParkBlockerHistory.bucket_start >= start,
            ParkBlockerHistory.bucket_start < end,
            ParkBlockerHistory.definition_version == 2,
            ParkBlockerHistory.scanned_at.is_not(None),
        )
    ).all()
    history = [
        row for row in history if as_utc(row.bucket_start) == align_bucket_start(row.bucket_start)
    ]
    # Source flow counters have no status dimension: never pretend to scope them.
    if allowed_statuses is not None:
        history = []
    snapshots = db.scalars(
        select(AnalyticsSnapshot)
        .where(
            AnalyticsSnapshot.park_id == park_id,
            AnalyticsSnapshot.bucket_start >= start,
            AnalyticsSnapshot.bucket_start < end,
        )
        .order_by(AnalyticsSnapshot.bucket_start)
    ).all()
    snapshots = [
        row for row in snapshots if align_bucket_start(row.observed_at) == as_utc(row.bucket_start)
    ]
    observation_query = select(AnalyticsObservation).where(
        AnalyticsObservation.park_id == park_id,
        AnalyticsObservation.bucket_start >= start,
        AnalyticsObservation.bucket_start < end,
    )
    if allowed_statuses is not None:
        # Apply authorization before any metric, duration or drilldown is built.
        observation_query = observation_query.where(
            AnalyticsObservation.authorization_status.in_(allowed_statuses)
        )
    observations = db.scalars(observation_query).all()
    issue_keys = {row.issue_key for row in observations}
    states = (
        {
            state.issue_key: state
            for state in db.scalars(
                select(TrackerIssueHistoryState).where(
                    TrackerIssueHistoryState.issue_key.in_(issue_keys)
                )
            ).all()
        }
        if issue_keys
        else {}
    )
    deadlines = {}
    invalid_history_timezone = False
    for key, state in states.items():
        if state.first_queued_at is None or not state.anchor_timezone:
            continue
        try:
            deadlines[key] = sla_clock.deadline(
                as_utc(state.first_queued_at), timezone=state.anchor_timezone
            )
        except (ValueError, ZoneInfoNotFoundError):
            # The queue timestamp still measures downtime, but an invalid
            # historical timezone cannot establish a working-hours deadline.
            invalid_history_timezone = True
            continue
    grouped = defaultdict(list)
    for row in observations:
        grouped[as_utc(row.bucket_start)].append(row)

    backlog, sla = {}, {}
    ages = {key: {} for key in [*AGE_BANDS, "unknown"]}
    # Presentation groups can differ from authorization statuses. Keep every
    # group represented by the already-authorized rows (e.g. queued + relocation).
    observed_stages = {row.status_bucket for row in observations}
    stages = [
        stage
        for stage in STAGES
        if allowed_statuses is None or stage in allowed_statuses or stage in observed_stages
    ]
    workload = {stage: {} for stage in stages}
    durations = defaultdict(list)
    previous = {}
    previous_bucket_start = None
    drilldown = set()
    for snapshot in snapshots:
        time = as_utc(snapshot.bucket_start)
        observed_at = as_utc(snapshot.observed_at)
        if previous_bucket_start is not None and time - previous_bucket_start != STEP:
            # A missing snapshot cannot establish when the status changed.
            previous.clear()
        previous_bucket_start = time
        rows = grouped[time]
        keys = tuple(sorted(row.issue_key for row in rows))
        drilldown.update(keys)
        backlog[time] = Sample(len(rows), keys, len(rows))
        queue_age = {
            row.issue_key: (
                observed_at - as_utc(states[row.issue_key].first_queued_at)
            ).total_seconds()
            / 3600
            for row in rows
            if row.issue_key in states
            and states[row.issue_key].first_queued_at is not None
            and as_utc(states[row.issue_key].first_queued_at) <= observed_at
        }
        for key, limits in AGE_BANDS.items():
            matching = [
                row
                for row in rows
                if row.issue_key in queue_age and limits[0] <= queue_age[row.issue_key] < limits[1]
            ]
            ages[key][time] = Sample(
                len(matching) if queue_age or not rows else None,
                tuple(row.issue_key for row in matching),
                len(matching),
                complete=len(queue_age) == len(rows),
            )
        unknown = [row for row in rows if row.issue_key not in queue_age]
        ages["unknown"][time] = Sample(
            len(unknown), tuple(row.issue_key for row in unknown), len(unknown)
        )
        # Only a complete Tracker changelog supplies an SLA anchor. Snapshot
        # creation age is never substituted for the first queue transition.
        known = [row for row in rows if row.issue_key in queue_age and row.issue_key in deadlines]
        overdue = [row.issue_key for row in known if observed_at > deadlines[row.issue_key]]
        sla[time] = Sample(
            len(overdue) if known else None,
            tuple(overdue),
            len(known),
            complete=len(known) == len(rows),
        )
        for stage in stages:
            matching = [row for row in rows if row.status_bucket == stage]
            workload[stage][time] = Sample(
                len(matching), tuple(row.issue_key for row in matching), len(matching)
            )
        # Absence in a successful snapshot breaks an observed residence interval.
        previous = {key: value for key, value in previous.items() if key in keys}
        for row in rows:
            prior = previous.get(row.issue_key)
            if prior and prior[0].status != row.status:
                hours = (observed_at - prior[1]).total_seconds() / 3600
                if hours > 0:
                    durations[prior[0].status_bucket].append((hours, row.issue_key))
            if prior is None or prior[0].status != row.status:
                previous[row.issue_key] = (row, observed_at)

    flow_coverage = coverage(start, end, len(history))
    state_coverage = coverage(start, end, len(snapshots))
    result_series = {
        key: series(
            key,
            "tasks",
            {as_utc(row.bucket_start): Sample(getattr(row, field)) for row in history},
            start,
            end,
            bucket,
            "sum",
        )
        for key, field in [("arrived", "arrived_count"), ("departed", "departed_count")]
    }
    result_series["backlog"] = series("backlog", "tasks_per_snapshot", backlog, start, end, bucket)
    stage_metrics = []
    for stage in stages:
        samples = durations[stage]
        stage_keys = sorted({key for _, key in samples})
        stage_metrics.append(
            AnalyticsMetric(
                **{
                    **state_coverage.model_dump(),
                    "complete": state_coverage.complete and bool(samples),
                },
                key=stage,
                unit="hours",
                value=sum(value for value, _ in samples) / len(samples) if samples else None,
                sample_count=len(samples),
                task_keys=stage_keys,
                task_keys_count=len(stage_keys),
            )
        )
    warnings = ["stage_durations_are_observed_estimates", "flow_counts_have_no_task_keys"]
    if not flow_coverage.complete:
        warnings.append("flow_history_incomplete")
    if not state_coverage.complete:
        warnings.append("observations_incomplete")
    if any(sample.value is None or not sample.complete for sample in sla.values()) or not sla:
        warnings.append("queue_history_unavailable")
    if invalid_history_timezone:
        warnings.append("invalid_history_timezone")
    if any(state.history_state == "forbidden" for state in states.values()):
        warnings.append("history_access_denied")
    if any(state.history_state == "retry" for state in states.values()):
        warnings.append("history_source_unavailable")
    if allowed_statuses is not None:
        warnings.append("flow_unavailable_for_status_scope")
        warnings.append("verified_closures_unavailable_for_status_scope")
    else:
        warnings.append("verified_closures_known_history_only")
        backfill = db.get(TrackerHistoryBackfillCursor, park_id)
        if backfill is not None and backfill.search_failed:
            warnings.append("closed_history_search_failed")
        if backfill is not None and backfill.page_cap_reached:
            warnings.append("closed_history_page_cap")
    drilldown_keys = sorted(drilldown)
    return AnalyticsOut(
        park_id=park_id,
        timezone=db.scalar(select(Park.timezone).where(Park.id == park_id)) or "Europe/Moscow",
        generated_at=now,
        period=AnalyticsPeriod(start=start, end=end),
        bucket=bucket,
        series=result_series,
        backlog_age_bands=[
            series(key, "tasks_per_snapshot", samples, start, end, bucket)
            for key, samples in ages.items()
        ],
        sla_trend=series("overdue_share", "percent", sla, start, end, bucket, "ratio"),
        stage_durations=stage_metrics,
        workload=[
            series(key, "tasks_per_snapshot", samples, start, end, bucket)
            for key, samples in workload.items()
        ],
        coverage={"flow": flow_coverage, "observations": state_coverage},
        drilldown_task_keys=drilldown_keys,
        drilldown_task_keys_count=len(drilldown_keys),
        verified_closures=verified_closures(
            db,
            park_id=park_id,
            start=start,
            end=end,
            scoped=allowed_statuses is not None,
        ),
        warnings=warnings,
    )
