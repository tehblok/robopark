"""Historical contract: null is unavailable; coverage always counts base 2h buckets."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AnalyticsBucket = Literal["2h", "1d"]


class AnalyticsPeriod(BaseModel):
    start: datetime
    end: datetime


class AnalyticsCoverage(BaseModel):
    period: AnalyticsPeriod
    observed_buckets: int
    expected_buckets: int
    complete: bool


class AnalyticsMetric(AnalyticsCoverage):
    key: str
    unit: Literal["tasks", "tasks_per_snapshot", "percent", "hours"]
    value: float | None
    sample_count: int = 0
    task_keys: list[str] = Field(default_factory=list)


class AnalyticsSeries(AnalyticsMetric):
    aggregation: Literal["sum", "mean", "ratio"]
    points: list[AnalyticsMetric]


class AnalyticsOut(BaseModel):
    park_id: int
    generated_at: datetime
    timezone: str = "Europe/Moscow"
    period: AnalyticsPeriod
    bucket: AnalyticsBucket
    observation_interval_hours: int = 2
    series: dict[str, AnalyticsSeries]
    backlog_age_bands: list[AnalyticsSeries]
    sla_trend: AnalyticsSeries
    stage_durations: list[AnalyticsMetric]
    workload: list[AnalyticsSeries]
    coverage: dict[str, AnalyticsCoverage]
    drilldown_task_keys: list[str]
    warnings: list[str]
