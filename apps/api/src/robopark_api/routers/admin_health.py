from fastapi import APIRouter, Depends, Response

from robopark_api.config import Settings, get_settings
from robopark_api.deps import require_admin
from robopark_api.services.operational_health import cached_host_snapshot
from robopark_api.services.ops.context import resolved_ops_dir

router = APIRouter(
    prefix="/admin/health", tags=["admin-health"], dependencies=[Depends(require_admin)]
)


@router.get("")
def host_health(response: Response, settings: Settings = Depends(get_settings)):
    response.headers["Cache-Control"] = "no-store"
    # A fresh database-backed authorization already succeeded for this request.
    return {
        **cached_host_snapshot(resolved_ops_dir(settings).parent, resolved_ops_dir(settings)),
        "database": "ok",
    }
