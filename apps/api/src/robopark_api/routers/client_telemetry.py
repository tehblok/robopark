import logging

from fastapi import APIRouter, Depends, status

from robopark_api.client_telemetry_schemas import (
    ClientTelemetryAcceptedOut,
    ClientTelemetryBatchIn,
)
from robopark_api.deps import require_user
from robopark_api.models import User

router = APIRouter(prefix="/client-telemetry", tags=["client-telemetry"])
logger = logging.getLogger(__name__)


@router.post("", response_model=ClientTelemetryAcceptedOut, status_code=status.HTTP_202_ACCEPTED)
def accept_client_telemetry(
    payload: ClientTelemetryBatchIn,
    _user: User = Depends(require_user),
) -> ClientTelemetryAcceptedOut:
    # Only enum names and numeric aggregates reach logs; task content and media are rejected by schema.
    totals: dict[str, float] = {}
    for metric in payload.metrics:
        totals[metric.name] = totals.get(metric.name, 0) + metric.value
    logger.info("client_metrics count=%s totals=%s", len(payload.metrics), totals)
    return ClientTelemetryAcceptedOut(accepted=len(payload.metrics))
