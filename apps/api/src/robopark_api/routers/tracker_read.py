from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import get_user_parks, require_user
from robopark_api.models import Park, User
from robopark_api.routers._blockers import blocker_out as _blocker_out
from robopark_api.schemas import (
    RobotTicketsOut,
    TrackerAttachmentOut,
    TrackerCommentOut,
    TrackerIssueCapabilitiesOut,
    TrackerIssueClaimOut,
    TrackerIssueDetailOut,
    TrackerIssueOut,
    TrackerIssuesOut,
    TrackerPersonOut,
    TrackerTransitionOut,
    TrackerUserOut,
)
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import (
    rbac,
    task_lifecycle,
    tracker_cache,
    tracker_client,
    tracker_filters,
    tracker_history,
)
from robopark_api.services import tracker_signatures as sig_svc
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.tracker_assignees import list_assignee_candidates
from robopark_api.services.tracker_claims import (
    get_claim,
    local_assignee,
    local_assignees,
    mechanic_can_access_issue,
    owned_issue_keys,
)
from robopark_api.services.tracker_policy import (
    allowed_park_tags_for_user,
    allowed_queues_for_user,
    can_view_untagged,
    can_write_tracker,
    enforce_issue_scope,
    is_issue_in_scope,
    issue_tags,
    load_issue_scope,
    park_tag_identity,
    park_tag_matches,
)
from robopark_api.task_workflow_models import ReliableAction, TaskReview

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
MAX_OWNED_QUERY_KEYS = 20
WORK_HISTORY_REQUEST_BUDGET_SECONDS = 0.25
WORK_HISTORY_STARTS_PER_REQUEST = 2

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tracker", tags=["tracker-read"])


def _issue_key_batches(keys: set[str]) -> list[list[str]]:
    ordered = sorted(keys)
    return [
        ordered[start : start + MAX_OWNED_QUERY_KEYS]
        for start in range(0, len(ordered), MAX_OWNED_QUERY_KEYS)
    ]


def _issue_key_clause(keys: list[str]) -> str:
    return "(" + " OR ".join(f"Key: {tracker_client.ql_token(key)}" for key in keys) + ")"


def _conditional_private_json(request: Request, response: Response, value: Any) -> Any:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    etag = f'"{hashlib.sha256(encoded.encode()).hexdigest()}"'
    headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    response.headers.update(headers)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return value


def _ensure_tracker_user(user: User, db: Session) -> None:
    rbac.assert_approved_or_staff(user)
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_READ):
        return
    if rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_WRITE):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def _enforce_mechanic_claim(db: Session, user: User, issue: dict) -> None:
    if not mechanic_can_access_issue(db, user, issue):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="tracker_issue_claim_required",
        )


def _person_out(raw: object) -> TrackerPersonOut | None:
    if not isinstance(raw, dict):
        return None
    display = str(raw.get("display") or "").strip()
    login = str(raw.get("login") or "").strip()
    if not display and not login:
        return None
    return TrackerPersonOut(display=display or login, login=login)


def _issue_out(
    issue: dict,
    *,
    db: Session | None = None,
    assignee_override: dict[str, str] | None = None,
    timezone: str | None = None,
) -> TrackerIssueOut:
    assignee = assignee_override
    if assignee is None and db is not None:
        assignee = local_assignee(db, issue)
    elif assignee is None and db is None:
        assignee = issue.get("assignee")
    anchor_timezone = (
        issue.get("sla_anchor_timezone") if "sla_anchor_timezone" in issue else timezone
    )
    sla = tracker_client.repair_sla_fields(issue, timezone=str(anchor_timezone or ""))
    return TrackerIssueOut(
        key=str(issue.get("key") or ""),
        summary=str(issue.get("summary") or ""),
        status=str(issue.get("status") or ""),
        status_key=str(issue.get("status_key") or ""),
        queue=str(issue.get("queue") or ""),
        robot=issue.get("robot"),
        created_at=issue.get("created"),
        updated_at=issue.get("updated"),
        hours_created=issue.get("hours_created"),
        url=tracker_client.build_issue_url(str(issue.get("key") or "")),
        tags=[str(tag) for tag in (issue.get("tags") or [])],
        priority=str(issue.get("priority") or ""),
        type=str(issue.get("type") or ""),
        assignee=_person_out(assignee),
        sla_timezone=str(anchor_timezone) if anchor_timezone else None,
        **sla,
    )


def _issue_park(issue: dict, parks: list[Park], selected_tag: str | None = None) -> Park | None:
    tags = issue_tags(issue)
    matches = [
        park
        for park in parks
        if park_tag_matches(park.tag, tags) and park.tracker_queue == issue.get("queue")
    ]
    if selected_tag:
        matches = [
            park
            for park in matches
            if park_tag_identity(park.tag) == park_tag_identity(selected_tag)
        ]
    return matches[0] if len(matches) == 1 else None


def _issue_park_timezone(
    issue: dict, parks: list[Park], selected_tag: str | None = None
) -> str | None:
    park = _issue_park(issue, parks, selected_tag)
    return park.timezone if park is not None else None


def _ordered_by_queue(items: list[dict], *, newest: bool, queue_first: bool = False) -> list[dict]:
    def timestamp(issue: dict) -> float | None:
        fields = tracker_client.repair_sla_fields(issue)
        verified = fields["queued_at"] if fields["sla_source"] == "status_history" else None
        # A queued task without a proven transition has unknown queue age.
        # Creation time may still order other statuses, but cannot prioritize it.
        raw = verified or (
            None
            if queue_first and tracker_filters.issue_status_bucket(issue) == "queued"
            else issue.get("created")
        )
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return None

    indexed = [(issue, index, timestamp(issue)) for index, issue in enumerate(items)]
    return [
        issue
        for issue, _index, _timestamp in sorted(
            indexed,
            key=lambda item: (
                0
                if not queue_first or tracker_filters.issue_status_bucket(item[0]) == "queued"
                else 1,
                item[2] is None,
                -(item[2] or 0) if newest else (item[2] or 0),
                item[1],
            ),
        )
    ]


def _work_issue_sla(
    *, token: str, issue: dict, db: Session | None = None, park: Park | None = None
) -> dict:
    embedded = tracker_client.repair_sla_fields(issue)
    if embedded["sla_source"] == "status_history" and (db is None or park is None):
        return {**issue, **embedded}
    verified = tracker_history.attach_verified_history(db, [issue])[0] if db else issue
    if verified.get("sla_source") == "status_history" and verified.get("sla_anchor_timezone"):
        return verified
    try:
        history = tracker_client.get_issue_status_history(
            token=token,
            key=str(issue.get("key") or ""),
            issue=issue,
        )
    except tracker_client.TrackerError:
        return verified if verified is not issue else {**issue, **embedded}
    if db is not None and park is not None:
        tracker_history.ingest_status_history(
            db,
            issue_key=str(issue.get("key") or ""),
            park=park,
            history=history,
            current_tags=issue_tags(issue),
        )
        return tracker_history.attach_verified_history(db, [issue])[0]
    return {
        **issue,
        **tracker_client.repair_sla_fields(issue, status_history=history),
    }


def _work_page_sla(
    *, token: str, issues: list[dict], db: Session, parks: list[Park], selected_tag=None
) -> list[dict]:
    return tracker_history.hydrate_visible_history(
        db,
        token=token,
        issues=issues,
        parks=parks,
        selected_tag=selected_tag,
        budget_seconds=WORK_HISTORY_REQUEST_BUDGET_SECONDS,
        max_starts=WORK_HISTORY_STARTS_PER_REQUEST,
    )


def _normalized_robot_number(raw: object) -> str | None:
    text = str(raw or "").strip().upper()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text[6:] if text.startswith("YASADR") else text.removeprefix("A")
    if not text or not text.isdecimal():
        return None
    return text.lstrip("0") or "0"


def _detail_out(
    issue: dict, *, db: Session, user: User, timezone: str | None, include_hidden: bool = False
) -> TrackerIssueDetailOut:
    task_lifecycle.reconcile_external_closure(db, issue)
    attachments = [TrackerAttachmentOut(**item) for item in (issue.get("attachments") or [])]
    claim = get_claim(db, str(issue.get("key") or ""))
    writable = can_write_tracker(db, user)
    workflow = task_lifecycle.workflow(
        db,
        issue_key=str(issue.get("key") or ""),
        viewer=user,
        issue=issue,
        include_hidden=include_hidden,
    )
    return TrackerIssueDetailOut(
        **_issue_out(issue, db=db, timezone=timezone).model_dump(),
        resolution=str(issue.get("resolution") or ""),
        description=str(issue.get("description") or ""),
        reporter=_person_out(issue.get("reporter")),
        components=[str(item) for item in (issue.get("components") or [])],
        attachments=attachments,
        claim=TrackerIssueClaimOut(park_id=claim.park_id, state=claim.state)
        if claim is not None
        else None,
        capabilities=TrackerIssueCapabilitiesOut(
            comment=writable,
            assign=False,
            unassign=False,
            transition=False,
            close=writable
            and rbac.role_slug(user) in {RoleSlug.OPERATOR, RoleSlug.ADMIN, RoleSlug.ROYAL}
            and workflow["review_state"] == "pending",
            attach=rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_ATTACH),
        ),
        workflow=workflow,
    )


def _untagged_park_tags(db: Session, user: User) -> list[str]:
    """Tags to exclude for «неразмеченные»: user's parks, or all active parks for admin."""
    tags = sorted(t for t in allowed_park_tags_for_user(db, user) if t)
    if tags:
        return tags
    if user.role in {RoleSlug.ADMIN, RoleSlug.ROYAL}:
        rows = db.scalars(select(Park).where(Park.is_active.is_(True))).all()
        return sorted(
            {str(park.tag).strip() for park in rows if park.tag and str(park.tag).strip()}
        )
    return []


def _build_query(
    *,
    user: User,
    db: Session,
    queue: str | None,
    park: str | None,
    status_filter: str | None,
    robot: str | None,
    assignee: str | None,
    untagged: bool,
    related_repairs: bool = False,
) -> str:
    parts: list[str] = [] if related_repairs else ["Priority: blocker"]
    # Explicit Status replaces the default open-issues clause (avoid conflicting QL).
    if status_filter:
        bucket = tracker_filters.status_bucket(status_filter)
        if user.role == RoleSlug.DRIVER and bucket not in {"new", "moving"}:
            raise HTTPException(status_code=403, detail="tracker_status_forbidden")
        aliases = tracker_filters.STATUS_BUCKETS.get(status_filter)
        if aliases:
            parts.append(
                "("
                + " OR ".join(f"Status: {tracker_client.ql_token(alias)}" for alias in aliases)
                + ")"
            )
        else:
            parts.append(f"Status: {tracker_client.ql_token(status_filter)}")
    else:
        parts.append(tracker_client.open_issues_clause())
    if user.role == RoleSlug.DRIVER:
        aliases = (
            *tracker_filters.STATUS_BUCKETS["new"],
            *tracker_filters.STATUS_BUCKETS["moving"],
        )
        parts.append(
            "("
            + " OR ".join(f"Status: {tracker_client.ql_token(alias)}" for alias in aliases)
            + ")"
        )

    queues = allowed_queues_for_user(db, user)
    selected_queue = (queue or "").strip() or None
    if user.role not in {RoleSlug.ADMIN, RoleSlug.ROYAL}:
        if not queues:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_scope_empty",
            )
        if selected_queue and selected_queue not in queues:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_queue_forbidden",
            )
        if selected_queue:
            parts.append(f"Queue: {tracker_client.ql_token(selected_queue)}")
        elif queues:
            if len(queues) == 1:
                selected_queue = queues[0]
                parts.append(f"Queue: {tracker_client.ql_token(selected_queue)}")
            else:
                parts.append(
                    "(" + " OR ".join(f"Queue: {tracker_client.ql_token(q)}" for q in queues) + ")"
                )
    elif selected_queue:
        parts.append(f"Queue: {tracker_client.ql_token(selected_queue)}")
    else:
        # Never run an unscoped search — default to fleet ops queue.
        selected_queue = tracker_client.DEFAULT_QUEUE
        parts.append(f"Queue: {tracker_client.ql_token(selected_queue)}")

    # Related repairs override fleet defaults, which can include non-repair types.
    if related_repairs:
        parts.append("Type: repair")
    elif selected_queue:
        type_part = tracker_client.type_clause(selected_queue)
        if type_part:
            parts.append(type_part)

    if robot:
        parts.append(f"Summary: {tracker_client.ql_quote(robot)}")

    assignee_part = tracker_client.assignee_clause(assignee or "")
    if assignee_part:
        parts.append(assignee_part)

    if park:
        allowed_tags = allowed_park_tags_for_user(db, user)
        if user.role not in {RoleSlug.ADMIN, RoleSlug.ROYAL} and not park_tag_matches(
            park, allowed_tags
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_park_forbidden",
            )
        parts.append(f"Tags: {tracker_client.ql_token(park)}")
    elif untagged:
        if not can_view_untagged(db, user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="tracker_untagged_forbidden",
            )
        if not selected_queue:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tracker_queue_required_for_untagged",
            )
        # Retain explicit status/robot/assignee filters while excluding park tags.
        parts.extend(tracker_client.exclude_tag(tag) for tag in _untagged_park_tags(db, user))

    return tracker_client.join_query(*parts)


@router.get("/issues", response_model=TrackerIssuesOut)
def list_issues(
    request: Request,
    response: Response,
    queue: str | None = Query(default=None),
    park: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    open_only: bool = Query(default=False),
    robot: str | None = Query(default=None),
    robot_exact: str | None = Query(
        default=None, max_length=tracker_client.MAX_ROBOT_REFERENCE_LENGTH
    ),
    related_repairs: bool = Query(default=False),
    exclude_key: str | None = Query(default=None, max_length=128),
    assignee: str | None = Query(default=None, max_length=128),
    untagged: bool = Query(default=False),
    age_hours: int | None = Query(default=None, ge=1),
    sort_order: Literal["oldest", "newest", "queue_first"] = Query(default="oldest", alias="sort"),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    include_hidden: bool = Query(default=False),
    owned_by_me: bool = Query(default=False),
    sync_state: Literal["needs_attention"] | None = Query(default=None),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerIssuesOut:
    _ensure_tracker_user(user, db)
    if include_hidden and not rbac.is_admin_or_royal(user):
        raise HTTPException(status_code=403, detail="task_hidden_manager_required")
    exact_robot = _normalized_robot_number(robot_exact)
    if related_repairs and exact_robot is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tracker_robot_exact_required",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    query_text = _build_query(
        user=user,
        db=db,
        queue=None if owned_by_me else queue,
        park=None if owned_by_me else park,
        status_filter=None if owned_by_me else status_filter,
        robot=robot,
        assignee=None if owned_by_me else assignee,
        untagged=False if owned_by_me else untagged,
        related_repairs=related_repairs,
    )
    if open_only and status_filter:
        query_text = tracker_client.join_query(query_text, tracker_client.open_issues_clause())
    if robot_exact is not None:
        if exact_robot is None:
            return _conditional_private_json(
                request,
                response,
                TrackerIssuesOut(items=[], total=0, limit=limit, offset=offset, has_more=False),
            )
        query_text = tracker_client.join_query(
            query_text, tracker_client.robot_summary_clause(exact_robot)
        )
    resolved_until = datetime.now(UTC)
    resolved_since = (
        resolved_until - timedelta(days=14)
        if related_repairs and (status_filter or "").strip().lower() == "closed"
        else None
    )
    if resolved_since is not None:
        # Keep the search cache reusable between pages. The exact rolling
        # boundary is applied below, including when a cached row ages out.
        query_since = resolved_since.replace(minute=0, second=0, microsecond=0)
        query_text = tracker_client.join_query(
            query_text, f'Resolved: >= "{query_since:%Y-%m-%d %H:%M:%S}"'
        )
    operator_owned = owned_by_me and rbac.role_slug(user) == RoleSlug.OPERATOR
    pending_review_keys = (
        set(
            db.scalars(
                select(TaskReview.issue_key).where(
                    TaskReview.reviewer_user_id == user.id,
                    TaskReview.state == "pending",
                )
            ).all()
        )
        if operator_owned
        else set()
    )
    owned_parks = (
        None if rbac.is_admin_or_royal(user) else {park.id for park in get_user_parks(db, user)}
    )
    try:
        for keys in _issue_key_batches(pending_review_keys):
            review_issues = tracker_cache.search_issues(
                token=token,
                query=_issue_key_clause(keys),
                filter_open=False,
                order=["createdAt"],
            )
            for review_issue in review_issues:
                task_lifecycle.reconcile_external_closure(db, review_issue)
        candidate_owned_keys = (
            set(
                db.scalars(
                    select(TaskReview.issue_key).where(
                        TaskReview.reviewer_user_id == user.id,
                        TaskReview.state == "pending",
                    )
                ).all()
            )
            if operator_owned
            else owned_issue_keys(db, user, park_ids=owned_parks)
            if owned_by_me
            else set()
        )
        items: list[dict] = []
        key_batches = _issue_key_batches(candidate_owned_keys) if owned_by_me else [None]
        base_query = query_text
        for keys in key_batches:
            scoped_query = base_query
            if keys is not None:
                scoped_query = tracker_client.join_query(base_query, _issue_key_clause(keys))
            query_text = scoped_query
            items.extend(
                tracker_cache.search_issues(
                    token=token,
                    query=scoped_query,
                    filter_open=owned_by_me or open_only or not bool(status_filter),
                    order=["createdAt"],
                )
            )
    except tracker_client.TrackerError as exc:
        logger.exception("tracker search failed query=%r", query_text)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc

    if related_repairs:
        keyed_items = sorted(items, key=lambda issue: str(issue.get("key") or ""))
        ordered = tracker_filters.sort_issues_oldest_first(keyed_items)
        if sort_order == "newest":
            ordered.reverse()
    else:
        ordered = _ordered_by_queue(
            items, newest=sort_order == "newest", queue_first=sort_order == "queue_first"
        )

    excluded_key = (exclude_key or "").strip()
    scoped_raw: list[dict] = []
    seen_keys: set[str] = set()
    hidden_keys = task_lifecycle.hidden_issue_keys(db)
    if operator_owned:
        owned_keys = set(
            db.scalars(
                select(TaskReview.issue_key).where(
                    TaskReview.reviewer_user_id == user.id,
                    TaskReview.state == "pending",
                )
            ).all()
        )
    else:
        owned_keys = owned_issue_keys(db, user, park_ids=owned_parks) if owned_by_me else set()
    # Raw upstream data is shared; authorization is loaded afresh for this
    # response after the upstream wait and reused only across its rows.
    scope = load_issue_scope(db, user)
    for issue in ordered:
        # Out-of-scope issues are filtered out, not fatal: a single foreign issue
        # in the upstream response must not fail the whole listing.
        if not is_issue_in_scope(db, user, issue, scope=scope):
            continue
        if related_repairs and str(issue.get("type_key") or "").strip() != "repair":
            continue
        if resolved_since is not None:
            try:
                resolved = datetime.fromisoformat(str(issue.get("resolved") or ""))
            except ValueError:
                continue
            if resolved.tzinfo is None:
                resolved = resolved.replace(tzinfo=UTC)
            if not resolved_since <= resolved <= resolved_until:
                continue
        if robot_exact is not None and (
            exact_robot is None or _normalized_robot_number(issue.get("robot")) != exact_robot
        ):
            continue
        key = str(issue.get("key") or "").strip()
        if key in hidden_keys and not include_hidden:
            continue
        if owned_by_me and key not in owned_keys:
            continue
        if key == excluded_key or key in seen_keys:
            continue
        if age_hours is not None:
            try:
                issue_age = float(issue.get("hours_created"))
            except (TypeError, ValueError):
                continue
            if not isfinite(issue_age) or issue_age < age_hours:
                continue
        seen_keys.add(key)
        scoped_raw.append(issue)

    if sync_state == "needs_attention":
        attention_keys = set(
            db.scalars(
                select(ReliableAction.resource_id).where(
                    ReliableAction.resource_type == "tracker_issue",
                    ReliableAction.state == "needs_attention",
                )
            ).all()
        )
        scoped_raw = [
            issue for issue in scoped_raw if str(issue.get("key") or "").strip() in attention_keys
        ]

    queue_age_order = not related_repairs and (
        sort_order == "queue_first"
        or (
            sort_order == "oldest"
            and tracker_filters.status_bucket(status_filter or "") == "queued"
        )
    )
    if queue_age_order:
        # The local ledger already knows some first queue transitions. Read it
        # in batches before pagination; this adds no Tracker requests.
        scoped_raw = _ordered_by_queue(
            tracker_history.attach_verified_history(db, scoped_raw),
            newest=False,
            queue_first=True,
        )

    total = len(scoped_raw)
    parks_for_sla = list(db.scalars(select(Park).where(Park.is_active.is_(True))).all())
    page_raw = _work_page_sla(
        token=token,
        db=db,
        parks=parks_for_sla,
        selected_tag=park,
        issues=(
            scoped_raw[offset : offset + limit]
            if queue_age_order
            else tracker_history.attach_verified_history(db, scoped_raw[offset : offset + limit])
        ),
    )
    if not related_repairs:
        page_raw = _ordered_by_queue(
            page_raw, newest=sort_order == "newest", queue_first=queue_age_order
        )
    assignments = local_assignees(db, [str(issue.get("key") or "") for issue in page_raw])
    page = [
        _issue_out(
            {
                **issue,
                "assignee": assignments.get(str(issue.get("key") or "")),
            },
            timezone=_issue_park_timezone(issue, parks_for_sla, park),
        )
        for issue in page_raw
    ]
    return _conditional_private_json(
        request,
        response,
        TrackerIssuesOut(
            items=page,
            total=total,
            limit=limit,
            offset=offset,
            has_more=offset + len(page) < total,
        ),
    )


@router.get("/users", response_model=list[TrackerUserOut])
def search_tracker_users(
    q: str = Query(min_length=1, max_length=64),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TrackerUserOut]:
    _ensure_tracker_user(user, db)
    return [TrackerUserOut(**item) for item in list_assignee_candidates(db, user, q)]


@router.get("/issues/{key}", response_model=TrackerIssueDetailOut)
def get_issue(
    key: str,
    include_hidden: bool = Query(default=False),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> TrackerIssueDetailOut:
    _ensure_tracker_user(user, db)
    if include_hidden and not rbac.is_admin_or_royal(user):
        raise HTTPException(status_code=403, detail="task_hidden_manager_required")
    if task_lifecycle.is_hidden(db, key) and not include_hidden:
        raise HTTPException(status_code=404)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )
    try:
        issue = tracker_cache.get_issue(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)
    _enforce_mechanic_claim(db, user, issue)
    parks_for_sla = list(db.scalars(select(Park).where(Park.is_active.is_(True))).all())
    issue = _work_issue_sla(token=token, issue=issue, db=db, park=_issue_park(issue, parks_for_sla))
    return _detail_out(
        issue,
        db=db,
        user=user,
        timezone=_issue_park_timezone(issue, parks_for_sla),
        include_hidden=include_hidden,
    )


@router.get("/issues/{key}/comments", response_model=list[TrackerCommentOut])
def get_comments(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TrackerCommentOut]:
    _ensure_tracker_user(user, db)
    if task_lifecycle.is_hidden(db, key):
        raise HTTPException(status_code=404)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    issue = tracker_cache.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)
    _enforce_mechanic_claim(db, user, issue)

    try:
        comments = tracker_cache.list_comments(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc
    if user.role == RoleSlug.MECHANIC:
        comments = sig_svc.filter_mechanic_visible_comments(db, comments)
    return [
        TrackerCommentOut(
            **{
                **item,
                "attachments": [
                    TrackerAttachmentOut(**attachment)
                    for attachment in (item.get("attachments") or [])
                ],
            }
        )
        for item in comments
    ]


@router.get("/transitions/{key}", response_model=list[TrackerTransitionOut])
def get_transitions(
    key: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> list[TrackerTransitionOut]:
    _ensure_tracker_user(user, db)
    if task_lifecycle.is_hidden(db, key):
        raise HTTPException(status_code=404)
    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    issue = tracker_cache.get_issue(token=token, key=key)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enforce_issue_scope(db, user, issue)
    _enforce_mechanic_claim(db, user, issue)

    try:
        transitions = tracker_cache.list_transitions(token=token, key=key)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc
    return [TrackerTransitionOut(**item) for item in transitions]


def _robot_search_queues(db: Session, user: User) -> list[str]:
    scoped = allowed_queues_for_user(db, user)
    if scoped:
        return scoped
    parks = (
        list(db.scalars(select(Park).where(Park.is_active.is_(True))).all())
        if rbac.is_admin_or_royal(user)
        else get_user_parks(db, user)
    )
    seen: set[str] = set()
    queues: list[str] = []
    for park in parks:
        queue = (park.tracker_queue or "").strip()
        if queue and queue not in seen:
            seen.add(queue)
            queues.append(queue)
    if queues:
        return queues
    if rbac.is_admin_or_royal(user):
        return [tracker_client.DEFAULT_QUEUE]
    return []


@router.get("/robots/{query}/tickets", response_model=RobotTicketsOut)
def robot_tickets(
    query: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
) -> RobotTicketsOut:
    _ensure_tracker_user(user, db)
    queues = _robot_search_queues(db, user)
    if not queues:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no_tracker_parks",
        )

    token = settings_svc.get_tracker_token(db)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tracker_token_not_configured",
        )

    allowed = set(queues)
    merged: list[dict] = []
    keys: set[str] = set()
    try:
        for queue in queues:
            for item in tracker_cache.search_robot_tickets(token=token, queue=queue, query=query):
                item_queue = (item.get("queue") or "").strip()
                if item_queue and item_queue not in allowed:
                    continue
                if item["key"] in keys:
                    continue
                keys.add(item["key"])
                merged.append(item)
    except tracker_client.TrackerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="tracker_upstream_error",
        ) from exc

    scope = load_issue_scope(db, user)
    sorted_items = tracker_filters.sort_issues_oldest_first(
        [
            item
            for item in merged
            if is_issue_in_scope(db, user, item, scope=scope)
            and not task_lifecycle.is_hidden(db, str(item.get("key") or ""))
        ]
    )
    return RobotTicketsOut(
        query=query,
        items=[_blocker_out(item) for item in sorted_items],
    )
