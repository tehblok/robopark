"""Tracker-derived registry: one paginated search, cache-only diagnostics."""

import re
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.deps import get_user_parks
from robopark_api.models import Park, User
from robopark_api.services import emergency_cache, rbac, tracker_cache, tracker_client
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.emergency_snapshot import parse_emergency_snapshot
from robopark_api.services.tracker_policy import issue_authorization_status, issue_tags

RegistryState = Literal["all", "online", "offline", "unknown", "errors", "tasks"]


@dataclass(frozen=True)
class RegistryAccess:
    """Request-local policy snapshot: no ORM objects or per-issue database reads."""

    permissions: frozenset[str]
    driver: bool
    parks: tuple[tuple[int, str, str], ...]

    def matching_parks(self, issue: dict) -> tuple[int, ...]:
        if self.driver and issue_authorization_status(issue) not in {"new", "moving"}:
            return ()
        queue = str(issue.get("queue") or "").strip()
        tags = issue_tags(issue)
        # Registry identities/counts require a provable active queue+park pair,
        # including the all-parks view and staff. Untagged Tracker work is not a roster.
        return tuple(
            park_id
            for park_id, park_queue, tag in self.parks
            if queue == park_queue and tag in tags
        )


def _vin(raw: object) -> str | None:
    text = str(raw or "").strip().upper()
    if re.fullmatch(r"YASADR[0-9]{11}", text):
        return text
    if re.fullmatch(r"[0-9]{1,11}", text):
        return f"YASADR{text.zfill(11)}"
    return None


def _parks(db: Session, user: User, park_id: int | None) -> list[Park]:
    parks = (
        list(db.scalars(select(Park).order_by(Park.id)))
        if rbac.is_admin_or_royal(user)
        else get_user_parks(db, user)
    )
    if park_id is not None:
        park = db.get(Park, park_id)
        if park is None:
            raise HTTPException(404, detail="park_not_found")
        if not park.is_active or park_id not in {p.id for p in parks}:
            raise HTTPException(403, detail="park_out_of_scope")
        return [park]
    return [park for park in parks if park.is_active]


def _access(db: Session, user: User, park_id: int | None) -> RegistryAccess:
    rbac.assert_approved(user)
    permissions = frozenset(rbac.permissions_for_user(db, user))
    if not {rbac.PERMISSION_NAV_ROBOT_SEARCH, rbac.PERMISSION_TRACKER_READ} <= permissions:
        raise HTTPException(403)
    return RegistryAccess(
        permissions=permissions,
        driver=rbac.role_slug(user) == rbac.RoleSlug.DRIVER,
        parks=tuple(
            (park.id, park.tracker_queue.strip(), park.tag.strip())
            for park in _parks(db, user, park_id)
            if park.tracker_queue and park.tracker_queue.strip() and park.tag and park.tag.strip()
        ),
    )


def registry_rows(
    db: Session,
    user: User,
    *,
    park_id: int | None,
    query: str,
    state: RegistryState,
    active_errors: bool,
    open_tasks: bool,
    offset: int,
    limit: int,
) -> dict:
    access = _access(db, user, park_id)
    queues = sorted({queue for _, queue, _ in access.parks})
    issues = []
    if queues:
        token = settings_svc.get_tracker_token(db)
        if not token:
            raise HTTPException(503, detail="tracker_token_not_configured")
        # The existing client drains all upstream pages. Group/count/filter only
        # after the full batch; offset/limit paginate robots, never Tracker issues.
        batch_query = " OR ".join(f"Queue: {tracker_client.ql_quote(queue)}" for queue in queues)
        try:
            issues = tracker_cache.search_issues(
                token=token, query=f"({batch_query})", filter_open=False
            )
        except tracker_client.TrackerError as exc:
            raise HTTPException(502, detail="tracker_upstream_error") from exc
    rows: dict[str, dict] = {}
    for issue in issues:
        matches = access.matching_parks(issue)
        if not matches:
            continue
        key = str(issue.get("key") or "").strip()
        vin = _vin(issue.get("robot"))
        if not vin or not key:
            continue
        row = rows.setdefault(
            vin,
            {
                "vin": vin,
                "short_number": vin[6:].lstrip("0") or "0",
                "park_ids": set(),
                "task_keys": set(),
                "issue_keys": set(),
            },
        )
        row["park_ids"].update(matches)
        row["issue_keys"].add(key)
        if tracker_client.is_issue_open_item(issue):
            row["task_keys"].add(key)
    identity = settings_svc.get_emergency_cookie_probe(db)[1]
    can_diagnose = rbac.PERMISSION_NAV_EMERGENCY in access.permissions
    cached = (
        emergency_cache.peek_robot_payloads(vins=list(rows), identity=identity)
        if can_diagnose
        else {}
    )
    search = query.strip().upper()
    query_vin = _vin(search)
    items = []
    partial = False
    for vin, row in rows.items():
        if search and not (query_vin == vin if query_vin else search in row["issue_keys"]):
            continue
        payload = cached.get(vin)
        telemetry = None
        error_count = None
        if payload is not None:
            snapshot = parse_emergency_snapshot(payload, vin=vin)
            telemetry = {
                "source": "emergency_cache",
                **{
                    field: snapshot[field]
                    for field in ("online", "charge_percent", "mode", "connection")
                },
            }
            # Count reported entries, not inferred health; missing fields stay unknown.
            if any(
                field in payload
                for field in (
                    "errors",
                    "wheelsBroken",
                    "lastCritNotification",
                    "lastErrorNotification",
                )
            ):
                errors = payload.get("errors") or []
                errors = errors if isinstance(errors, list) else [errors]
                reported = {str(value) for value in errors if value}
                if not reported and snapshot["error_banner"]:
                    reported.add(snapshot["error_banner"])
                reported.update(f"wheel:{slot}" for slot in snapshot["wheels_fault"])
                error_count = len(reported)
        availability = (
            "unknown"
            if telemetry is None or telemetry["online"] is None
            else "online"
            if telemetry["online"]
            else "offline"
        )
        partial = partial or telemetry is None or error_count is None or availability == "unknown"
        task_count = len(row["task_keys"])
        if state in {"online", "offline", "unknown"} and state != availability:
            continue
        if (active_errors or state == "errors") and not error_count:
            continue
        if (open_tasks or state == "tasks") and not task_count:
            continue
        items.append(
            {
                **row,
                "park_ids": sorted(row["park_ids"]),
                "task_keys": sorted(row["task_keys"]),
                "issue_keys": sorted(row["issue_keys"]),
                "task_count": task_count,
                "error_count": error_count,
                "telemetry": telemetry,
                "state": availability,
            }
        )
    items.sort(key=lambda row: int(row["short_number"]))
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < len(items),
        "source_complete": True,
        "partial": partial,
        "source": "scoped_tracker_issues",
        "park_id": park_id,
    }
