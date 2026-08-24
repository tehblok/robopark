from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import (
    get_mechanic_park,
    require_approved_mechanic,
    require_approved_operator,
    require_user,
)
from robopark_api.models import AccessStatus, Report, User, UserRole
from robopark_api.schemas import (
    ReportBadgeOut,
    ReportCreateIn,
    ReportEscalateIn,
    ReportFromTicketCloseIn,
    ReportOut,
    ReportReturnIn,
)
from robopark_api.services import reports as reports_svc
from robopark_api.services import tracker_client

router = APIRouter(prefix="/reports", tags=["reports"])

T = TypeVar("T")


def _require_inbox_viewer(user: User = Depends(require_user)) -> User:
    if user.role in (UserRole.admin.value, UserRole.royal.value):
        return user
    if (
        user.role == UserRole.operator.value
        and user.access_status == AccessStatus.approved.value
    ):
        return user
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _report_out(report: Report) -> ReportOut:
    return ReportOut.model_validate(report)


def _run_svc(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc


def _require_mechanic_park(db: Session, user: User, park_id: int) -> None:
    park = get_mechanic_park(db, user)
    if park is None or park.id != park_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: ReportCreateIn,
    user: User = Depends(require_approved_mechanic),
    db: Session = Depends(get_db),
) -> ReportOut:
    _require_mechanic_park(db, user, payload.park_id)
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
    return _report_out(report)


@router.post("/from-ticket-close", response_model=ReportOut)
def create_from_ticket_close(
    payload: ReportFromTicketCloseIn,
    user: User = Depends(require_approved_mechanic),
    db: Session = Depends(get_db),
) -> ReportOut:
    park = get_mechanic_park(db, user)
    if park is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    tracker_url = payload.tracker_url or tracker_client.build_issue_url(
        payload.tracker_key
    )
    title = payload.title or f"Закрытие {payload.tracker_key}"
    report = _run_svc(
        lambda: reports_svc.get_or_create_close_review(
            db,
            author=user,
            park_id=park.id,
            tracker_key=payload.tracker_key,
            tracker_url=tracker_url,
            title=title,
            body=payload.body,
        )
    )
    return _report_out(report)


@router.get("/inbox", response_model=list[ReportOut])
def list_inbox(
    park_id: int | None = Query(default=None),
    user: User = Depends(_require_inbox_viewer),
    db: Session = Depends(get_db),
) -> list[ReportOut]:
    reports = _run_svc(
        lambda: reports_svc.list_inbox(db, user, park_id=park_id)
    )
    return [_report_out(report) for report in reports]


@router.get("/mine", response_model=list[ReportOut])
def list_mine(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[ReportOut]:
    reports = reports_svc.list_mine(db, user)
    return [_report_out(report) for report in reports]


@router.get("/badge", response_model=ReportBadgeOut)
def report_badge(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReportBadgeOut:
    counts = reports_svc.badge_counts(db, user)
    return ReportBadgeOut(count=counts["count"])


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(lambda: reports_svc.get_report(db, user, report_id))
    return _report_out(report)


@router.post("/{report_id}/return", response_model=ReportOut)
def return_report(
    report_id: int,
    payload: ReportReturnIn,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(
        lambda: reports_svc.return_report(
            db, user, report_id, payload.comment
        )
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


@router.post("/{report_id}/escalate", response_model=ReportOut)
def escalate_report(
    report_id: int,
    payload: ReportEscalateIn,
    user: User = Depends(require_approved_operator),
    db: Session = Depends(get_db),
) -> ReportOut:
    report = _run_svc(
        lambda: reports_svc.escalate_report(
            db, user, report_id, payload.comment
        )
    )
    return _report_out(report)
