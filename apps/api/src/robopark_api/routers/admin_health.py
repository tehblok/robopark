from pathlib import Path

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from robopark_api.config import Settings, get_settings
from robopark_api.db import get_db
from robopark_api.deps import require_admin
from robopark_api.services.operational_health import cached_host_snapshot
from robopark_api.services.ops.context import resolved_ops_dir
from robopark_api.services.sync_health import sync_health

router = APIRouter(
    prefix="/admin/health", tags=["admin-health"], dependencies=[Depends(require_admin)]
)


@router.get("")
def host_health(
    response: Response,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "no-store"
    # A fresh database-backed authorization already succeeded for this request.
    return {
        **cached_host_snapshot(
            Path(settings.host_data_path),
            resolved_ops_dir(settings),
            Path(settings.host_health_path),
        ),
        "database": "ok",
        "sync": sync_health(db).model_dump(mode="json"),
    }
