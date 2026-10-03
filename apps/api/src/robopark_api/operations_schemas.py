"""Shared Overview/Analytics contract. Null means unavailable, never an invented zero."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from robopark_api.schemas import BlockerOut


class StatusOptionOut(BaseModel):
    key: str
    label: str


class FlowPointOut(BaseModel):
    bucket_start: datetime
    arrived_count: int
    departed_count: int


class FlowOut(BaseModel):
    definition_version: Literal[2] = 2
    window_start: datetime
    window_end: datetime
    expected_buckets: int
    observed_buckets: int
    complete: bool
    legacy_buckets: int
    points: list[FlowPointOut]


class OverdueTaskOut(BlockerOut):
    age_hours: float
    overdue_hours: float


class TaskTimingOut(BaseModel):
    issue_key: str
    queue_started_at: datetime | None
    sla_deadline: datetime | None
    sla_working_hours: float | None
    sla_timezone: str | None = None
    downtime_hours: float | None


class SlaOut(BaseModel):
    target_hours: int | None
    evaluated_count: int
    unknown_count: int
    at_risk_count: int | None
    overdue_count: int | None
    overdue: list[OverdueTaskOut]
    overdue_truncated: bool


class WorkloadOut(BaseModel):
    login: str | None
    display: str
    open_count: int
    overdue_count: int | None
    oldest_hours: float | None


class OperatorLoadOut(BaseModel):
    user_id: int
    username: str
    tracker_login: str | None
    open_count: int | None
    overdue_count: int | None
    oldest_hours: float | None


class OperationsOverviewOut(BaseModel):
    park_id: int
    generated_at: datetime
    timezone: str
    status_options: list[StatusOptionOut]
    selected_status: str
    counts: dict[str, int]
    tasks: list[BlockerOut]
    task_timing: list[TaskTimingOut]
    tasks_total: int
    tasks_truncated: bool
    flow: FlowOut
    sla: SlaOut
    workload: list[WorkloadOut] | None
    operators: list[OperatorLoadOut] | None
