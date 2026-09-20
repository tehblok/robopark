from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MetricName = Literal[
    "startup_ms",
    "queue_length",
    "storage_bytes",
    "retry_count",
    "migration_failure",
]


class ClientMetricIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: MetricName
    value: float = Field(ge=0, le=10**12)


class ClientTelemetryBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[ClientMetricIn] = Field(min_length=1, max_length=20)


class ClientTelemetryAcceptedOut(BaseModel):
    accepted: int
