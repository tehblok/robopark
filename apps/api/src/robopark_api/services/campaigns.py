from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from robopark_api.models import (
    AccessStatus,
    Campaign,
    CampaignPark,
    CampaignSubmission,
    Park,
    Report,
    User,
    UserPark,
)
from robopark_api.services import audit, tracker_cache, tracker_client
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import report_attachments as attachment_svc
from robopark_api.services.rbac import RoleSlug

KIND_SERVICE_COMPANY = "service_company"
KIND_WRAPPING = "wrapping"
KINDS = frozenset({KIND_SERVICE_COMPANY, KIND_WRAPPING})
REPORT_KIND = "campaign_review"


def _approved(user: User) -> bool:
    return bool(user.is_active and user.access_status == AccessStatus.approved.value)


def _manager(user: User) -> bool:
    return _approved(user) and user.role in {RoleSlug.ADMIN, RoleSlug.ROYAL}


def _accessible_park_ids(db: Session, user: User) -> set[int]:
    if not _approved(user):
        return set()
    stmt = select(Park.id).where(Park.is_active.is_(True))
    if user.role not in {RoleSlug.ADMIN, RoleSlug.ROYAL}:
        stmt = stmt.join(UserPark).where(UserPark.user_id == user.id)
    return set(db.scalars(stmt))


def _campaign(db: Session, campaign_id: int) -> Campaign:
    row = db.scalar(
        select(Campaign).options(selectinload(Campaign.parks)).where(Campaign.id == campaign_id)
    )
    if row is None:
        raise LookupError("campaign_not_found")
    return row


def _scoped_parks(
    db: Session, user: User, campaign: Campaign, only_park_id: int | None = None
) -> list[Park]:
    allowed = _accessible_park_ids(db, user)
    if only_park_id is not None:
        if only_park_id not in allowed:
            raise PermissionError("forbidden")
        allowed = {only_park_id}
    parks = [park for park in campaign.parks if park.id in allowed and park.is_active]
    if not parks:
        raise PermissionError("forbidden")
    return sorted(parks, key=lambda park: (park.name.casefold(), park.id))


def _normalize_text(value: str, error: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(error)
    return text


def _validated_parks(db: Session, park_ids: list[int]) -> list[Park]:
    unique = sorted(set(park_ids))
    if not unique or len(unique) != len(park_ids):
        raise ValueError("campaign_parks_invalid")
    parks = list(
        db.scalars(
            select(Park).where(Park.id.in_(unique), Park.is_active.is_(True)).order_by(Park.id)
        ).all()
    )
    if len(parks) != len(unique):
        raise ValueError("campaign_parks_invalid")
    return parks


def _validate_dates(starts_on: date, due_on: date) -> None:
    if due_on < starts_on:
        raise ValueError("campaign_dates_invalid")


def create_campaign(
    db: Session,
    user: User,
    *,
    kind: str,
    name: str,
    tracker_tag: str,
    park_ids: list[int],
    starts_on: date,
    due_on: date,
) -> Campaign:
    if not _manager(user):
        raise PermissionError("forbidden")
    if kind not in KINDS:
        raise ValueError("campaign_kind_invalid")
    _validate_dates(starts_on, due_on)
    parks = _validated_parks(db, park_ids)
    row = Campaign(
        kind=kind,
        name=_normalize_text(name, "campaign_name_required"),
        tracker_tag=_normalize_text(tracker_tag, "campaign_tag_required"),
        starts_on=starts_on,
        due_on=due_on,
        created_by=user.id,
        parks=parks,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _campaign(db, row.id)


def update_campaign(db: Session, user: User, campaign_id: int, changes: dict) -> Campaign:
    if not _manager(user):
        raise PermissionError("forbidden")
    row = _campaign(db, campaign_id)
    if changes.get("name") is not None:
        row.name = _normalize_text(changes["name"], "campaign_name_required")
    if changes.get("tracker_tag") is not None:
        row.tracker_tag = _normalize_text(changes["tracker_tag"], "campaign_tag_required")
    if changes.get("park_ids") is not None:
        row.parks = _validated_parks(db, changes["park_ids"])
    if changes.get("starts_on") is not None:
        row.starts_on = changes["starts_on"]
    if changes.get("due_on") is not None:
        row.due_on = changes["due_on"]
    if changes.get("is_active") is not None:
        row.is_active = bool(changes["is_active"])
    _validate_dates(row.starts_on, row.due_on)
    db.commit()
    return _campaign(db, row.id)


def campaign_shell(campaign: Campaign) -> dict:
    parks = sorted(campaign.parks, key=lambda park: (park.name.casefold(), park.id))
    return {
        "id": campaign.id,
        "kind": campaign.kind,
        "name": campaign.name,
        "tracker_tag": campaign.tracker_tag,
        "starts_on": campaign.starts_on,
        "due_on": campaign.due_on,
        "is_active": campaign.is_active,
        "park_ids": [park.id for park in parks],
        "park_names": [park.name for park in parks],
        "total_count": 0,
        "completed_count": 0,
        "pending_review_count": 0,
        "remaining_count": 0,
        "percent_complete": 0,
        "overdue": False,
    }


def _has_tags(issue: dict, campaign_tag: str, park_tag: str) -> bool:
    tags = {str(value).strip().casefold() for value in issue.get("tags") or []}
    return campaign_tag.casefold() in tags and park_tag.casefold() in tags


def _fetch_issues(db: Session, campaign: Campaign, parks: list[Park]) -> list[tuple[dict, Park]]:
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise RuntimeError("tracker_token_not_configured")
    found: dict[str, tuple[dict, Park]] = {}
    for park in parks:
        queue = (park.tracker_queue or tracker_client.DEFAULT_QUEUE).strip()
        query = tracker_client.join_query(
            f"Queue: {tracker_client.ql_token(queue)}",
            f"Tags: {tracker_client.ql_token(campaign.tracker_tag)}",
            f"Tags: {tracker_client.ql_token(park.tag)}",
        )
        try:
            issues = tracker_cache.search_issues(token=token, query=query, filter_open=False)
        except tracker_client.TrackerError as exc:
            raise RuntimeError("tracker_upstream_error") from exc
        for issue in issues:
            key = str(issue.get("key") or "").strip()
            if key and _has_tags(issue, campaign.tracker_tag, park.tag):
                found.setdefault(key, (issue, park))
    return sorted(found.values(), key=lambda pair: str(pair[0].get("key") or ""))


def _tracker_closed(issue: dict) -> bool:
    if str(issue.get("resolution") or "").strip() or str(issue.get("resolved") or "").strip():
        return True
    status = f"{issue.get('status_key') or ''} {issue.get('status') or ''}".casefold().replace(
        "ё", "е"
    )
    return any(
        word in status for word in ("closed", "закрыт", "resolved", "решен", "cancelled", "отменен")
    )


def _submissions(db: Session, campaign_id: int) -> dict[str, tuple[CampaignSubmission, Report]]:
    rows = db.execute(
        select(CampaignSubmission, Report)
        .join(Report, Report.id == CampaignSubmission.report_id)
        .where(CampaignSubmission.campaign_id == campaign_id)
    ).all()
    return {submission.issue_key: (submission, report) for submission, report in rows}


def campaign_detail(
    db: Session, user: User, campaign_id: int, only_park_id: int | None = None
) -> dict:
    campaign = _campaign(db, campaign_id)
    parks = _scoped_parks(db, user, campaign, only_park_id)
    submissions = _submissions(db, campaign.id)
    tickets: list[dict] = []
    pending = 0
    completed = 0
    for issue, park in _fetch_issues(db, campaign, parks):
        key = str(issue.get("key") or "")
        submitted = submissions.get(key)
        submission, report = submitted if submitted else (None, None)
        locally_closed = bool(report and report.status in {"open", "done"})
        closed = locally_closed or _tracker_closed(issue)
        if closed:
            completed += 1
        if report and report.status == "open":
            pending += 1
        tickets.append(
            {
                "key": key,
                "summary": str(issue.get("summary") or ""),
                "status": str(issue.get("status") or ""),
                "park_id": park.id,
                "park_name": park.name,
                "robot": str(issue.get("robot") or "").strip() or None,
                "url": tracker_client.build_issue_url(key),
                "completed_at": submission.completed_at if submission else None,
                "completed_by": submission.author_user_id if submission else None,
                "comment": submission.comment if submission else None,
                "report_id": report.id if report else None,
                "review_status": report.status
                if report
                else ("tracker_closed" if closed else None),
                "tracker_transition": submission.tracker_transition if submission else None,
                "closed": closed,
            }
        )
    total = len(tickets)
    remaining = total - completed
    percent = round(completed * 100 / total) if total else 0
    return {
        "id": campaign.id,
        "kind": campaign.kind,
        "name": campaign.name,
        "tracker_tag": campaign.tracker_tag,
        "starts_on": campaign.starts_on,
        "due_on": campaign.due_on,
        "is_active": campaign.is_active,
        "park_ids": [park.id for park in parks],
        "park_names": [park.name for park in parks],
        "total_count": total,
        "completed_count": completed,
        "pending_review_count": pending,
        "remaining_count": remaining,
        "percent_complete": percent,
        "overdue": bool(date.today() > campaign.due_on and remaining > 0),
        "open_tickets": [
            {key: value for key, value in ticket.items() if key != "closed"}
            for ticket in tickets
            if not ticket["closed"]
        ],
        "closed_tickets": [
            {key: value for key, value in ticket.items() if key != "closed"}
            for ticket in tickets
            if ticket["closed"]
        ],
    }


def list_campaigns(db: Session, user: User, park_id: int | None = None) -> list[dict]:
    allowed = _accessible_park_ids(db, user)
    if park_id is not None:
        if park_id not in allowed:
            raise PermissionError("forbidden")
        allowed = {park_id}
    if not allowed:
        return []
    ids = list(
        db.scalars(
            select(Campaign.id)
            .join(CampaignPark)
            .where(CampaignPark.park_id.in_(allowed))
            .distinct()
            .order_by(Campaign.is_active.desc(), Campaign.due_on, Campaign.id)
        ).all()
    )
    return [
        {
            key: value
            for key, value in campaign_detail(db, user, campaign_id, park_id).items()
            if key not in {"open_tickets", "closed_tickets"}
        }
        for campaign_id in ids
    ]


def complete_ticket(
    db: Session,
    user: User,
    campaign_id: int,
    issue_key: str,
    *,
    park_id: int,
    comment: str,
    filename: str | None,
    content: bytes,
    content_type: str | None,
) -> CampaignSubmission:
    campaign = _campaign(db, campaign_id)
    parks = _scoped_parks(db, user, campaign)
    park = next((item for item in parks if item.id == park_id), None)
    if not campaign.is_active or park is None:
        raise PermissionError("forbidden")
    text = _normalize_text(comment, "comment_required")
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise RuntimeError("tracker_token_not_configured")
    try:
        issue = tracker_cache.get_issue(token=token, key=issue_key)
    except tracker_client.TrackerError as exc:
        raise RuntimeError("tracker_upstream_error") from exc
    if issue is None:
        raise LookupError("campaign_ticket_not_found")
    if not _has_tags(issue, campaign.tracker_tag, park.tag):
        raise PermissionError("forbidden")
    existing = db.scalar(
        select(CampaignSubmission).where(
            CampaignSubmission.campaign_id == campaign.id,
            CampaignSubmission.issue_key == issue_key,
        )
    )
    if existing is not None:
        previous = db.get(Report, existing.report_id)
        if previous is not None and previous.status != "returned":
            raise ValueError("campaign_ticket_already_completed")

    report = Report(
        kind=REPORT_KIND,
        status="open",
        park_id=park.id,
        author_user_id=user.id,
        target_role=RoleSlug.OPERATOR,
        tracker_key=issue_key,
        tracker_url=tracker_client.build_issue_url(issue_key),
        title=f"Проверка: {campaign.name} · {issue_key}",
        body=f"Кампания: {campaign.name}\nПарк: {park.name}\nРобот: {issue.get('robot') or 'не указан'}\n\n{text}",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    try:
        attachment_svc.add_attachment(
            db,
            user,
            report.id,
            kind=attachment_svc.KIND_DEVICE_PHOTO,
            filename=filename,
            content=content,
            content_type=content_type,
        )
    except Exception:
        db.delete(report)
        db.commit()
        raise

    transition_state = "unavailable"
    try:
        transitions = tracker_cache.list_transitions(token=token, key=issue_key)
        selected = None
        for marker in ("провер", "review", "verify", "диагност", "diagnos"):
            selected = next(
                (
                    item
                    for item in transitions
                    if marker in f"{item.get('id') or ''} {item.get('display') or ''}".casefold()
                ),
                None,
            )
            if selected is not None:
                break
        if selected is not None:
            transition_id = str(selected.get("id") or "").strip()
            tracker_client.transition_issue(token=token, key=issue_key, transition=transition_id)
            tracker_cache.invalidate_issue(issue_key)
            transition_text = (
                f"{selected.get('id') or ''} {selected.get('display') or ''}".casefold()
            )
            transition_state = (
                "review"
                if any(value in transition_text for value in ("провер", "review", "verify"))
                else "diagnostics"
            )
    except tracker_client.TrackerError:
        transition_state = "failed"
    report.body += f"\n\nTracker: {transition_state}"

    now = datetime.now(UTC)
    if existing is None:
        existing = CampaignSubmission(
            campaign_id=campaign.id,
            issue_key=issue_key,
            park_id=park.id,
            robot=str(issue.get("robot") or "").strip() or None,
            comment=text,
            author_user_id=user.id,
            report_id=report.id,
            tracker_transition=transition_state,
            completed_at=now,
        )
        db.add(existing)
    else:
        existing.park_id = park.id
        existing.robot = str(issue.get("robot") or "").strip() or None
        existing.comment = text
        existing.author_user_id = user.id
        existing.report_id = report.id
        existing.tracker_transition = transition_state
        existing.completed_at = now
    db.commit()
    db.refresh(existing)
    audit.record(
        db,
        action=audit.ACTION_TRACKER_TRANSITION,
        actor=user,
        park_id=park.id,
        target_type="tracker_issue",
        target_id=issue_key,
        outcome=(
            audit.OUTCOME_SUCCESS
            if transition_state in {"review", "diagnostics"}
            else audit.OUTCOME_FAILURE
        ),
        detail=f"campaign={campaign.id}; transition={transition_state}",
    )
    return existing
