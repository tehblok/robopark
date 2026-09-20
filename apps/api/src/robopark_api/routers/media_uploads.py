from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.media_schemas import (
    MediaChunkOut,
    MediaCompleteOut,
    MediaUploadCreateIn,
    MediaUploadSessionOut,
)
from robopark_api.models import User
from robopark_api.services import media_uploads, rbac

router = APIRouter(prefix="/media/uploads", tags=["media-uploads"])


@router.post("", response_model=MediaUploadSessionOut, status_code=status.HTTP_201_CREATED)
def create_upload(
    payload: MediaUploadCreateIn, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    rbac.assert_approved_or_staff(user)
    row = media_uploads.start(db, user, payload)
    return MediaUploadSessionOut(
        upload_id=row.id,
        received_offset=row.received_offset,
        completed=row.completed,
        media_id=row.media_id if row.completed else None,
    )


@router.put("/{upload_id}/chunks/{offset}", response_model=MediaChunkOut)
async def put_chunk(
    upload_id: str,
    offset: int,
    request: Request,
    chunk_sha256: str | None = Header(default=None, alias="X-Chunk-SHA256"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not chunk_sha256:
        raise HTTPException(400, "media_chunk_checksum_required")
    content = await request.body()
    received = media_uploads.append_chunk(db, user, upload_id, offset, content, chunk_sha256)
    return MediaChunkOut(received_offset=received)


@router.post("/{upload_id}/complete", response_model=MediaCompleteOut)
def complete_upload(
    upload_id: str, user: User = Depends(require_user), db: Session = Depends(get_db)
):
    row = media_uploads.complete(db, user, upload_id)
    return MediaCompleteOut(upload_id=row.id, media_id=row.media_id)
