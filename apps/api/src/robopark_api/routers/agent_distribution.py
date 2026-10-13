"""Public, bounded distribution assets; client verifies the pinned release signature."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/distribution/v1/macos", tags=["agent-distribution"])
_PACKAGE = re.compile(r"Robopark-[A-Za-z0-9._+-]+-arm64\.pkg\Z")


def distribution_root() -> Path:
    external = Path("/data/macos-distribution")
    if external.is_dir():
        return external
    return Path(__file__).resolve().parent.parent / "agent_distribution_assets"


@router.get("/{filename}")
def get_asset(filename: str):
    catalog = filename == "catalog.json"
    if not catalog and _PACKAGE.fullmatch(filename) is None:
        raise HTTPException(404, "asset_not_found")
    root = distribution_root().resolve()
    source = root / filename
    limit = 256 * 1024 if catalog else 512 * 1024 * 1024
    try:
        if (
            source.is_symlink()
            or not source.is_file()
            or source.resolve().parent != root
            or not 0 < source.stat().st_size <= limit
        ):
            raise HTTPException(404, "asset_not_found")
    except OSError as exc:
        raise HTTPException(404, "asset_not_found") from exc
    return FileResponse(
        source,
        media_type="application/json" if catalog else "application/octet-stream",
        filename=None if catalog else filename,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
