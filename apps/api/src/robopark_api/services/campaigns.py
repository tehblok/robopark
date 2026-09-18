from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from robopark_api.models import (
    AccessStatus,
    Campaign,
    CampaignPark,
    CampaignSnapshotTicket,
    CampaignSubmission,
    Park,
    Report,
    User,
    UserPark,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import report_attachments as attachment_svc
from robopark_api.services import tracker_cache, tracker_client
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.reliable_actions import begin_action

KIND_SERVICE_COMPANY = "service_company"
KIND_WRAPPING = "wrapping"
KINDS = frozenset({KIND_SERVICE_COMPANY, KIND_WRAPPING})
REPORT_KIND = "campaign_review"
REFRESH_INTERVAL = timedelta(minutes=2)
RETRY_INTERVAL = timedelta(minutes=1)
LEASE_INTERVAL = timedelta(seconds=90)
logger = logging.getLogger(__name__)


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


def _summary_rule(value: str) -> str:
    text = " ".join(_normalize_text(value, "campaign_tag_required").split())
    if len(text) < 3:
        raise ValueError("campaign_search_too_short")
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
        tracker_tag=_summary_rule(tracker_tag),
        selection_mode="summary",
        snapshot_state="pending",
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
    if row.archived_at is not None:
        raise ValueError("campaign_archived")
    rule_changed = False
    if changes.get("name") is not None:
        row.name = _normalize_text(changes["name"], "campaign_name_required")
    if changes.get("tracker_tag") is not None:
        search = (
            _summary_rule(changes["tracker_tag"])
            if row.selection_mode == "summary"
            else _normalize_text(changes["tracker_tag"], "campaign_tag_required")
        )
        if search != row.tracker_tag:
            row.tracker_tag = search
            rule_changed = True
    if changes.get("park_ids") is not None:
        next_parks = _validated_parks(db, changes["park_ids"])
        if {park.id for park in next_parks} != {park.id for park in row.parks}:
            row.parks = next_parks
            rule_changed = True
    if changes.get("starts_on") is not None:
        row.starts_on = changes["starts_on"]
    if changes.get("due_on") is not None:
        row.due_on = changes["due_on"]
    if changes.get("is_active") is not None:
        row.is_active = bool(changes["is_active"])
    _validate_dates(row.starts_on, row.due_on)
    if rule_changed:
        row.rule_revision += 1
        row.snapshot_state = "pending"
        row.snapshot_error = None
        row.snapshot_retry_at = None
    db.commit()
    return _campaign(db, row.id)


def delete_campaign(db: Session, user: User, campaign_id: int) -> str:
    if not _manager(user):
        raise PermissionError("forbidden")
    row = _campaign(db, campaign_id)
    has_results = (
        db.scalar(
            select(CampaignSubmission.id)
            .where(CampaignSubmission.campaign_id == campaign_id)
            .limit(1)
        )
        is not None
    )
    if has_results:
        row.archived_at = datetime.now(UTC)
        row.is_active = False
        row.snapshot_state = "idle"
        db.commit()
        return "archived"
    db.delete(row)
    db.commit()
    return "deleted"


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
        "snapshot_at": campaign.snapshot_at,
        "snapshot_state": campaign.snapshot_state,
        "snapshot_error": campaign.snapshot_error,
    }


def _normalized(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _matches_issue(issue: dict, campaign: Campaign, park: Park) -> bool:
    tags = {str(value).strip().casefold() for value in issue.get("tags") or []}
    if park.tag.casefold() not in tags:
        return False
    if campaign.selection_mode == "summary":
        return _normalized(campaign.tracker_tag) in _normalized(issue.get("summary") or "")
    return campaign.tracker_tag.casefold() in tags


def _fetch_issues(db: Session, campaign: Campaign, parks: list[Park]) -> list[tuple[dict, Park]]:
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise RuntimeError("tracker_token_not_configured")
    found: dict[str, tuple[dict, Park]] = {}
    for park in parks:
        queue = (park.tracker_queue or tracker_client.DEFAULT_QUEUE).strip()
        query = tracker_client.join_query(
            f"Queue: {tracker_client.ql_token(queue)}",
            (
                f"Summary: {tracker_client.ql_quote(campaign.tracker_tag)}"
                if campaign.selection_mode == "summary"
                else f"Tags: {tracker_client.ql_token(campaign.tracker_tag)}"
            ),
            f"Tags: {tracker_client.ql_token(park.tag)}",
        )
        try:
            issues = tracker_cache.search_issues(token=token, query=query, filter_open=False)
        except tracker_client.TrackerError as exc:
            raise RuntimeError("tracker_upstream_error") from exc
        for issue in issues:
            key = str(issue.get("key") or "").strip()
            if key and _matches_issue(issue, campaign, park):
                found.setdefault(key, (issue, park))
    return sorted(found.values(), key=lambda pair: str(pair[0].get("key") or ""))


def _snapshot_issues(db: Session, campaign: Campaign, parks: list[Park]) -> list[tuple[dict, Park]]:
    scoped = {park.id: park for park in parks}
    rows = db.scalars(
        select(CampaignSnapshotTicket)
        .where(
            CampaignSnapshotTicket.campaign_id == campaign.id,
            CampaignSnapshotTicket.rule_revision == campaign.rule_revision,
            CampaignSnapshotTicket.park_id.in_(scoped),
        )
        .order_by(CampaignSnapshotTicket.issue_key)
    ).all()
    return [
        (
            {
                "key": row.issue_key,
                "summary": row.summary,
                "status": row.status,
                "status_key": row.status_key,
                "resolution": row.resolution,
                "robot": row.robot,
            },
            scoped[row.park_id],
        )
        for row in rows
    ]


def request_refresh(db: Session, user: User, campaign_id: int) -> Campaign:
    campaign = _campaign(db, campaign_id)
    _scoped_parks(db, user, campaign)
    if campaign.is_active and campaign.snapshot_state != "running":
        campaign.snapshot_state = "pending"
        campaign.snapshot_error = None
        campaign.snapshot_retry_at = None
        db.commit()
    return campaign


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def process_refresh_batch(session_factory) -> int:
    """Claim one due campaign; never hold a DB write lock across Tracker I/O."""
    now = datetime.now(UTC)
    with session_factory() as db:
        candidate = db.scalar(
            select(Campaign)
            .where(
                Campaign.is_active.is_(True),
                (Campaign.snapshot_state == "pending")
                | ((Campaign.snapshot_state == "running") & (Campaign.snapshot_lease_until < now))
                | ((Campaign.snapshot_state == "idle") & (Campaign.snapshot_at.is_(None)))
                | ((Campaign.snapshot_state == "ready") & (Campaign.snapshot_retry_at <= now))
                | ((Campaign.snapshot_state == "error") & (Campaign.snapshot_retry_at <= now)),
            )
            .order_by(Campaign.id)
        )
        if candidate is None:
            return 0
        campaign_id, revision = candidate.id, candidate.rule_revision
        lease_until = now + LEASE_INTERVAL
        claimed = db.execute(
            update(Campaign)
            .where(
                Campaign.id == campaign_id,
                Campaign.rule_revision == revision,
                Campaign.snapshot_state == candidate.snapshot_state,
                Campaign.snapshot_lease_until.is_(candidate.snapshot_lease_until)
                if candidate.snapshot_lease_until is None
                else Campaign.snapshot_lease_until == candidate.snapshot_lease_until,
            )
            .values(snapshot_state="running", snapshot_lease_until=lease_until)
        ).rowcount
        db.commit()
        if not claimed:
            return 0

    try:
        with session_factory() as db:
            campaign = _campaign(db, campaign_id)
            parks = sorted(campaign.parks, key=lambda park: park.id)
            found = _fetch_issues(db, campaign, parks)
        with session_factory() as db:
            campaign = db.get(Campaign, campaign_id)
            if (
                campaign is None
                or campaign.rule_revision != revision
                or _utc(campaign.snapshot_lease_until) != lease_until
            ):
                return 1
            db.execute(
                delete(CampaignSnapshotTicket).where(
                    CampaignSnapshotTicket.campaign_id == campaign_id
                )
            )
            for issue, park in found:
                db.add(
                    CampaignSnapshotTicket(
                        campaign_id=campaign_id,
                        rule_revision=revision,
                        issue_key=str(issue.get("key") or "")[:128],
                        park_id=park.id,
                        summary=str(issue.get("summary") or "")[:512],
                        status=str(issue.get("status") or "")[:128],
                        status_key=str(issue.get("status_key") or "")[:128] or None,
                        resolution=str(issue.get("resolution") or "")[:128] or None,
                        robot=str(issue.get("robot") or "")[:64] or None,
                    )
                )
            campaign.snapshot_at = datetime.now(UTC)
            campaign.snapshot_state = "ready"
            campaign.snapshot_error = None
            campaign.snapshot_retry_at = campaign.snapshot_at + REFRESH_INTERVAL
            campaign.snapshot_lease_until = None
            db.commit()
    except Exception as exc:  # noqa: BLE001 - preserve last known snapshot on upstream failure
        logger.warning("Campaign %s refresh failed: %s", campaign_id, type(exc).__name__)
        with session_factory() as db:
            campaign = db.get(Campaign, campaign_id)
            if (
                campaign is not None
                and campaign.rule_revision == revision
                and _utc(campaign.snapshot_lease_until) == lease_until
            ):
                campaign.snapshot_state = "error"
                campaign.snapshot_error = "tracker_unavailable"
                campaign.snapshot_retry_at = datetime.now(UTC) + RETRY_INTERVAL
                campaign.snapshot_lease_until = None
                db.commit()
    return 1


async def run_refresh_loop(session_factory, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        processed = 0
        try:
            processed = await asyncio.to_thread(process_refresh_batch, session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Campaign refresh cycle failed")
        with suppress(TimeoutError):
            # Drain a backlog without five seconds of idle time per campaign;
            # retain a small pause so refreshes cannot hammer Tracker.
            await asyncio.wait_for(stop_event.wait(), timeout=0.1 if processed else 5.0)


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
    for issue, park in _snapshot_issues(db, campaign, parks):
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
        "snapshot_at": campaign.snapshot_at,
        "snapshot_state": campaign.snapshot_state,
        "snapshot_error": campaign.snapshot_error,
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
            .where(CampaignPark.park_id.in_(allowed), Campaign.archived_at.is_(None))
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
    idempotency_key: str | None = None,
) -> CampaignSubmission:
    if idempotency_key is not None and not 8 <= len(idempotency_key) <= 128:
        raise ValueError("campaign_completion_key_invalid")
    text = _normalize_text(comment, "comment_required")
    payload_hash = hashlib.sha256(
        f"{campaign_id}:{issue_key}:{park_id}:{text}:{hashlib.sha256(content).hexdigest()}".encode()
    ).hexdigest()
    # Serialize competing completions before reading the unique submission row.
    # SQLite's deferred read transaction cannot safely upgrade after another write.
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection().connection
        raw = getattr(connection, "driver_connection", connection)
        if not raw.in_transaction:
            raw.execute("BEGIN IMMEDIATE")
    else:
        db.execute(select(Campaign.id).where(Campaign.id == campaign_id).with_for_update())
    campaign = _campaign(db, campaign_id)
    parks = _scoped_parks(db, user, campaign)
    park = next((item for item in parks if item.id == park_id), None)
    if not campaign.is_active or park is None:
        raise PermissionError("forbidden")
    issue = db.scalar(
        select(CampaignSnapshotTicket).where(
            CampaignSnapshotTicket.campaign_id == campaign.id,
            CampaignSnapshotTicket.issue_key == issue_key,
            CampaignSnapshotTicket.park_id == park_id,
            CampaignSnapshotTicket.rule_revision == campaign.rule_revision,
        )
    )
    if issue is None:
        raise LookupError("campaign_ticket_not_found")
    existing = db.scalar(
        select(CampaignSubmission).where(
            CampaignSubmission.campaign_id == campaign.id,
            CampaignSubmission.issue_key == issue_key,
        )
    )
    if existing is not None:
        if idempotency_key and existing.completion_key == idempotency_key:
            if existing.completion_hash != payload_hash:
                raise HTTPException(409, "campaign_completion_payload_conflict")
            return existing
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
        body=f"Кампания: {campaign.name}\nПарк: {park.name}\nРобот: {issue.robot or 'не указан'}\n\n{text}",
    )
    attachment = None
    try:
        db.add(report)
        db.flush()
        attachment = attachment_svc.add_attachment(
            db,
            user,
            report.id,
            kind=attachment_svc.KIND_DEVICE_PHOTO,
            filename=filename,
            content=content,
            content_type=content_type,
            commit=False,
        )
        now = datetime.now(UTC)
        if existing is None:
            existing = CampaignSubmission(
                campaign_id=campaign.id,
                issue_key=issue_key,
                park_id=park.id,
                robot=issue.robot,
                comment=text,
                author_user_id=user.id,
                report_id=report.id,
                tracker_transition="pending",
                completion_key=idempotency_key,
                completion_hash=payload_hash,
                completed_at=now,
            )
            db.add(existing)
        else:
            existing.park_id = park.id
            existing.robot = issue.robot
            existing.comment = text
            existing.author_user_id = user.id
            existing.report_id = report.id
            existing.tracker_transition = "pending"
            existing.completion_key = idempotency_key
            existing.completion_hash = payload_hash
            existing.completed_at = now
        db.flush()
        begin_action(
            db,
            actor=user,
            resource_type="tracker_issue",
            resource_id=issue_key,
            action="campaign_review",
            idempotency_key=f"campaign-{campaign.id}-report-{report.id}",
            payload={
                "campaign_submission_id": existing.id,
                "report_id": report.id,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        if attachment is not None:
            with suppress(OSError, LookupError):
                attachment_svc._resolve_storage_key(attachment.storage_key).unlink()
        raise
    db.refresh(existing)
    return existing
