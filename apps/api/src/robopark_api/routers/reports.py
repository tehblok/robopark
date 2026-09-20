from collections.abc import Callable
from typing import TypeVar

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import Report, User
from robopark_api.schemas import (
    ReportAttachmentOut,
    ReportBadgeOut,
    ReportCreateIn,
    ReportEscalateIn,
    ReportOut,
    ReportResubmitIn,
    ReportReturnIn,
)
from robopark_api.services import rbac
from robopark_api.services import report_attachments as att_svc
from robopark_api.services import reports as reports_svc

router = APIRouter(prefix="/reports", tags=["reports"])

T = TypeVar("T")


def _require_inbox_viewer(
    user: User = Depends(require_user), db: Session = Depends(get_db)
) -> User:
    rbac.require_approved_permission(db, user, rbac.PERMISSION_REPORTS_RESOLVE)
    return user


def _require_report_author(
    user: User = Depends(require_user), db: Session = Depends(get_db)
) -> User:
    rbac.require_approved_permission(db, user, rbac.PERMISSION_REPORTS_CREATE)
    return user


def _report_out(report: Report) -> ReportOut:
    return ReportOut.model_validate(report)


def _run_svc(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except reports_svc.ReportLinkedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="report_not_found"
        ) from None
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden") from None
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="report_delete_storage_unavailable",
        ) from exc


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: ReportCreateIn,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(_require_report_author),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(
        lambda: reports_svc.create_manual_report(
            db,
            author=user,
            park_id=payload.park_id,
            kind=payload.kind,
            title=payload.title,
            body=payload.body,
            tracker_key=payload.tracker_key,
            tracker_url=payload.tracker_url,
        )
    )
    background_tasks.add_task(
        request.app.state.push_service.emit,
        event_type="report",
        park_id=report.park_id,
        protected_text=f"Новый репорт: {report.title}",
    )
    return _report_out(report)


@router.get("/inbox", response_model=list[ReportOut])
def list_inbox(
    park_id: int | None = Query(default=None),
    user: User = Depends(_require_inbox_viewer),
    db: Session = Depends(get_db),
) -> list[ReportOut]:
    reports = _run_svc(lambda: reports_svc.list_inbox(db, user, park_id=park_id))
    return [_report_out(report) for report in reports]


@router.get("/mine", response_model=list[ReportOut])
def list_mine(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[ReportOut]:
    reports = _run_svc(lambda: reports_svc.list_mine(db, user))
    return [_report_out(report) for report in reports]


@router.get("/badge", response_model=ReportBadgeOut)
def report_badge(
    park_id: int | None = Query(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReportBadgeOut:
    counts = _run_svc(lambda: reports_svc.badge_counts(db, user, park_id=park_id))
    return ReportBadgeOut(count=counts["count"])


@router.post(
    "/{report_id}/attachments",
    response_model=ReportAttachmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_report_attachment(
    report_id: int,
    kind: str = Form(...),
    file: UploadFile = File(...),
    user: User = Depends(_require_report_author),
    db: Session = Depends(get_db),
) -> ReportAttachmentOut:
    limit = _run_svc(lambda: att_svc.max_bytes_for_kind(kind))
    content = await file.read(limit + 1)
    row = _run_svc(
        lambda: att_svc.add_attachment(
            db,
            user,
            report_id,
            kind=kind,
            filename=file.filename,
            content=content,
            content_type=file.content_type,
        )
    )
    return ReportAttachmentOut.model_validate(row)


@router.get("/{report_id}/attachments/{attachment_id}")
def download_report_attachment(
    report_id: int,
    attachment_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    row, path = _run_svc(lambda: att_svc.get_attachment(db, user, report_id, attachment_id))
    return FileResponse(path, media_type=row.content_type, filename=row.filename)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(lambda: reports_svc.get_report(db, user, report_id))
    return _report_out(report)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    report_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> None:
    _run_svc(lambda: reports_svc.delete_report(db, user, report_id))


@router.post("/{report_id}/return", response_model=ReportOut)
def return_report(
    report_id: int,
    payload: ReportReturnIn,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(lambda: reports_svc.return_report(db, user, report_id, payload.comment))
    background_tasks.add_task(
        request.app.state.push_service.emit,
        event_type="return",
        park_id=report.park_id,
        protected_text=f"Репорт возвращён: {report.title}",
        target_user_ids={report.author_user_id},
    )
    return _report_out(report)


@router.post("/{report_id}/done", response_model=ReportOut)
def done_report(
    report_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(lambda: reports_svc.done_report(db, user, report_id))
    return _report_out(report)


@router.post("/{report_id}/resubmit", response_model=ReportOut)
def resubmit_report(
    report_id: int,
    payload: ReportResubmitIn,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(_require_report_author),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(
        lambda: reports_svc.resubmit_report(
            db,
            user,
            report_id,
            title=payload.title,
            body=payload.body,
            tracker_key=payload.tracker_key,
            tracker_url=payload.tracker_url,
        )
    )
    background_tasks.add_task(
        request.app.state.push_service.emit,
        event_type="report",
        park_id=report.park_id,
        protected_text=f"Репорт отправлен повторно: {report.title}",
    )
    return _report_out(report)


@router.post("/{report_id}/escalate", response_model=ReportOut)
def escalate_report(
    report_id: int,
    payload: ReportEscalateIn,
    user: User = Depends(_require_inbox_viewer),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(lambda: reports_svc.escalate_report(db, user, report_id, payload.comment))
    return _report_out(report)
