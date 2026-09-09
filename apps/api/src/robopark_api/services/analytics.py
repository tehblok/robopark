"""Aggregate measured history without filling missing buckets with zeros."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.analytics_schemas import (
    AnalyticsBucket,
    AnalyticsCoverage,
    AnalyticsMetric,
    AnalyticsOut,
    AnalyticsPeriod,
    AnalyticsSeries,
)
from robopark_api.models import AnalyticsObservation, AnalyticsSnapshot, ParkBlockerHistory
from robopark_api.services.blocker_history import align_bucket_start
from robopark_api.services.operations import STATUS_LABELS, as_utc, queued_working_hours

STEP = timedelta(hours=2)
STAGES = [key for key in STATUS_LABELS if key != "all"]
AGE_BANDS = {"under_24h": (0, 24), "24_to_72h": (24, 72), "over_72h": (72, float("inf"))}


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


def metric(key, unit, samples, start, end, aggregation) -> AnalyticsMetric:
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
    return AnalyticsMetric(
        **coverage(
            start, end, len(measured), complete=all(s.complete for s in measured)
        ).model_dump(),
        key=key,
        unit=unit,
        value=value,
        sample_count=count,
        task_keys=sorted({key for sample in measured for key in sample.keys}),
    )


def series(key, unit, samples, start, end, bucket, aggregation="mean") -> AnalyticsSeries:
    step = STEP if bucket == "2h" else timedelta(days=1)
    points = []
    cursor = start
    while cursor < end:
        points.append(metric(key, unit, samples, cursor, min(cursor + step, end), aggregation))
        cursor += step
    return AnalyticsSeries(
        **metric(key, unit, samples, start, end, aggregation).model_dump(),
        aggregation=aggregation,
        points=points,
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
    drilldown = set()
    for snapshot in snapshots:
        time = as_utc(snapshot.bucket_start)
        observed_at = as_utc(snapshot.observed_at)
        rows = grouped[time]
        keys = tuple(sorted(row.issue_key for row in rows))
        drilldown.update(keys)
        backlog[time] = Sample(len(rows), keys, len(rows))
        known_age = [row for row in rows if row.age_hours is not None]
        for key, limits in AGE_BANDS.items():
            matching = [
                row
                for row in rows
                if row.age_hours is not None and limits[0] <= row.age_hours < limits[1]
            ]
            ages[key][time] = Sample(
                len(matching) if known_age or not rows else None,
                tuple(row.issue_key for row in matching),
                len(matching),
                complete=len(known_age) == len(rows),
            )
        unknown = [row for row in rows if row.age_hours is None]
        ages["unknown"][time] = Sample(
            len(unknown), tuple(row.issue_key for row in unknown), len(unknown)
        )
        evaluated = []
        overdue = []
        for row in rows:
            if (
                row.status_bucket != "queued"
                or row.age_hours is None
                or snapshot.target_hours is None
            ):
                continue
            created = observed_at - timedelta(hours=row.age_hours)
            sla_age = queued_working_hours(
                {"status_key": "queued", "status": "queued", "created": created.isoformat()},
                observed_at,
            )
            if sla_age is None:
                continue
            evaluated.append(row)
            if sla_age > snapshot.target_hours:
                overdue.append(row)
        sla[time] = Sample(
            len(overdue) if evaluated else None,
            tuple(row.issue_key for row in overdue),
            len(evaluated),
            complete=len(evaluated) == len(rows),
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
                task_keys=sorted({key for _, key in samples}),
            )
        )
    warnings = ["stage_durations_are_observed_estimates", "flow_counts_have_no_task_keys"]
    if not flow_coverage.complete:
        warnings.append("flow_history_incomplete")
    if not state_coverage.complete:
        warnings.append("observations_incomplete")
    if any(sample.value is None or not sample.complete for sample in sla.values()) or not sla:
        warnings.append("sla_policy_or_age_unavailable")
    if allowed_statuses is not None:
        warnings.append("flow_unavailable_for_status_scope")
    return AnalyticsOut(
        park_id=park_id,
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
        drilldown_task_keys=sorted(drilldown),
        warnings=warnings,
    )
