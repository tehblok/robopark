from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import (
    require_approved_operator,
    require_operator_park,
    require_user,
)
from robopark_api.models import Report, User
from robopark_api.services import rbac
from robopark_api.schemas import (
    ReportBadgeOut,
    ReportCreateIn,
    ReportEscalateIn,
    ReportOut,
    ReportReturnIn,
)
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
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _require_mechanic_park(db: Session, user: User, park_id: int) -> None:
    require_operator_park(park_id, db, user)


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: ReportCreateIn,
    user: User = Depends(_require_report_author),
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
    reports = reports_svc.list_mine(db, user)
    return [_report_out(report) for report in reports]


@router.get("/badge", response_model=ReportBadgeOut)
def report_badge(
    park_id: int | None = Query(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> ReportBadgeOut:
    counts = _run_svc(lambda: reports_svc.badge_counts(db, user, park_id=park_id))
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
    report = _run_svc(lambda: reports_svc.return_report(db, user, report_id, payload.comment))
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
    report = _run_svc(lambda: reports_svc.escalate_report(db, user, report_id, payload.comment))
    return _report_out(report)
