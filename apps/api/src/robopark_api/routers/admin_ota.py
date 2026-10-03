"""Royal-only resumable upload of unified hash-verified OTA packages."""

from __future__ import annotations

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from robopark_api.config import Settings, get_settings
from robopark_api.deps import require_royal
from robopark_api.models import User
from robopark_api.services.ops import host_bridge
from robopark_api.services.ops.context import resolved_ops_dir
from robopark_api.services.ops.ota_uploads import OtaUploadError, OtaUploadStore

router = APIRouter(prefix="/admin/ops/ota", tags=["ops"])


class UploadCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=5, max_length=180)
    size: int = Field(strict=True, gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def upload_store(settings: Settings) -> OtaUploadStore:
    root = host_bridge.host_root(settings)
    return OtaUploadStore(
        resolved_ops_dir(settings) / "ota-uploads",
        root / "ota-uploads",
        max_bytes=settings.ops_max_upload_bytes,
    )


def _error(error: OtaUploadError) -> HTTPException:
    detail = str(error)
    code = (
        404
        if detail == "ota_upload_not_found"
        else 403
        if detail == "ota_upload_forbidden"
        else 409
        if detail
        in {
            "ota_offset_mismatch",
            "ota_upload_expired",
            "ota_upload_finalized",
            "ota_upload_incomplete",
            "ota_upload_quota",
        }
        else 413
        if detail in {"ota_package_too_large", "ota_chunk_too_large"}
        else 507
        if detail == "ota_insufficient_space"
        else 400
    )
    return HTTPException(status_code=code, detail=detail)


def _public(record) -> dict:
    value = asdict(record)
    value["upload_id"] = str(record.upload_id)
    value["changes"] = list(record.changes)
    value["compatible_from"] = list(record.compatible_from)
    return value


@router.get("/uploads")
def list_uploads(
    royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
):
    try:
        return {
            "items": [
                _public(record) for record in upload_store(settings).list_owned(actor_id=royal.id)
            ]
        }
    except OtaUploadError as exc:
        raise _error(exc) from exc


@router.post("/uploads", status_code=status.HTTP_201_CREATED)
def create_upload(
    payload: UploadCreateIn,
    royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
):
    try:
        return _public(upload_store(settings).create(actor_id=royal.id, **payload.model_dump()))
    except OtaUploadError as exc:
        raise _error(exc) from exc


@router.head("/uploads/{upload_id}")
def upload_status(
    upload_id: UUID,
    royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
):
    try:
        record = upload_store(settings).status(upload_id, actor_id=royal.id)
    except OtaUploadError as exc:
        raise _error(exc) from exc
    return Response(
        status_code=200,
        headers={
            "Upload-Offset": str(record.offset),
            "Upload-Length": str(record.size),
            "Upload-Expires": str(record.expires_at),
            "Cache-Control": "no-store",
        },
    )


@router.patch("/uploads/{upload_id}")
async def append_upload(
    upload_id: UUID,
    request: Request,
    royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
):
    if (
        request.headers.get("content-type", "").split(";", 1)[0]
        != "application/offset+octet-stream"
    ):
        raise HTTPException(status_code=415, detail="ota_content_type_required")
    try:
        offset = int(request.headers.get("upload-offset", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ota_offset_required") from exc
    store = upload_store(settings)
    chunk = bytearray()
    async for part in request.stream():
        chunk.extend(part)
        if len(chunk) > store.chunk_bytes:
            raise HTTPException(status_code=413, detail="ota_chunk_too_large")
    try:
        new_offset = store.append(upload_id, actor_id=royal.id, offset=offset, chunk=bytes(chunk))
    except OtaUploadError as exc:
        raise _error(exc) from exc
    return {"offset": new_offset}


@router.post("/uploads/{upload_id}/finalize")
def finalize_upload(
    upload_id: UUID,
    royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
):
    try:
        return _public(upload_store(settings).finalize(upload_id, actor_id=royal.id))
    except OtaUploadError as exc:
        raise _error(exc) from exc


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_upload(
    upload_id: UUID,
    royal: User = Depends(require_royal),
    settings: Settings = Depends(get_settings),
):
    try:
        upload_store(settings).delete(upload_id, actor_id=royal.id)
    except OtaUploadError as exc:
        raise _error(exc) from exc
    return Response(status_code=204)
