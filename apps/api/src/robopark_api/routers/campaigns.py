from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from robopark_api.campaign_schemas import (
    CampaignCreateIn,
    CampaignDetailOut,
    CampaignOut,
    CampaignSubmissionOut,
    CampaignUpdateIn,
)
from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import User
from robopark_api.services import campaigns as service
from robopark_api.services import report_attachments as attachment_svc

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
T = TypeVar("T")


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError:
        raise HTTPException(status_code=403, detail="forbidden") from None
    except RuntimeError as exc:
        detail = str(exc)
        code = 503 if detail == "tracker_token_not_configured" else 502
        raise HTTPException(status_code=code, detail=detail) from exc


@router.get("", response_model=list[CampaignOut])
def list_campaigns(
    park_id: int | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return _run(lambda: service.list_campaigns(db, user, park_id))


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: CampaignCreateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(lambda: service.create_campaign(db, user, **payload.model_dump()))
    return service.campaign_shell(row)


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdateIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(
        lambda: service.update_campaign(
            db, user, campaign_id, payload.model_dump(exclude_unset=True)
        )
    )
    return service.campaign_shell(row)


@router.delete("/{campaign_id}")
def delete_campaign(
    campaign_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return {"result": _run(lambda: service.delete_campaign(db, user, campaign_id))}


@router.get("/{campaign_id}", response_model=CampaignDetailOut)
def campaign_detail(
    campaign_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    return _run(lambda: service.campaign_detail(db, user, campaign_id))


@router.post("/{campaign_id}/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_campaign(
    campaign_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _run(lambda: service.request_refresh(db, user, campaign_id))
    return {"snapshot_state": row.snapshot_state, "snapshot_at": row.snapshot_at}


@router.post(
    "/{campaign_id}/tickets/{issue_key}/complete",
    response_model=CampaignSubmissionOut,
    status_code=status.HTTP_201_CREATED,
)
async def complete_campaign_ticket(
    campaign_id: int,
    issue_key: str,
    park_id: int = Form(...),
    comment: str = Form(..., min_length=1, max_length=4000),
    photo: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    content = await photo.read(attachment_svc.MAX_ATTACHMENT_BYTES + 1)
    row = _run(
        lambda: service.complete_ticket(
            db,
            user,
            campaign_id,
            issue_key,
            park_id=park_id,
            comment=comment,
            filename=photo.filename,
            content=content,
            content_type=photo.content_type,
            idempotency_key=idempotency_key,
        )
    )
    return CampaignSubmissionOut(
        id=row.id,
        issue_key=row.issue_key,
        report_id=row.report_id,
        review_status="open",
        tracker_transition=row.tracker_transition,
        completed_at=row.completed_at,
    )
