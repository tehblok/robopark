"""One task source and robot-visibility policy for the web and Telegram.

Only presentation adapters and user-selected history windows live in callers.
Authorization is recomputed on every call, including cache hits.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException

from robopark_api.services import task_lifecycle, tracker_cache, tracker_client
from robopark_api.services.tracker_policy import (
    enforce_issue_scope,
    is_issue_in_scope,
    is_related_robot_issue_in_scope,
    issue_tags,
    load_related_robot_scope,
    park_tag_identity,
    related_robot_anchors,
)

ROBOT_TASK_TYPES = frozenset({"repair", "service", "calibration"})
MAX_READ_ROBOT_IDENTITIES = 64
MAX_READ_PROOF_QUERY_LENGTH = 16 * 1024


def _visible_rows(db, rows, *, include_hidden=False):
    hidden = set() if include_hidden else task_lifecycle.hidden_issue_keys(db)
    return [row for row in rows if str(row.get("key") or "") not in hidden]


def _compact_robot_identity_clause(issue: dict, numbers: tuple[str, ...]) -> str:
    summaries: set[str] = set()
    rovers: set[str] = set()
    for number in numbers:
        summaries.update({number, f"a{number}", f"а{number}", f"YASADR{number.zfill(11)}"})
        rovers.update(tracker_client.robot_rover_variants(number))
    raw_values = [
        issue.get("robot"),
        tracker_client.parse_robot_from_summary(str(issue.get("summary") or "")),
    ]
    raw_values.extend(tracker_client.robot_field_values(issue.get("rover")))
    for raw in raw_values:
        value = str(raw or "").strip()
        if value and tracker_client.normalize_robot_reference(value) in numbers:
            rovers.add(value)
    clauses = [f"Summary: {tracker_client.ql_quote(value)}" for value in sorted(summaries)]
    clauses.extend(f"rover: {tracker_client.ql_quote(value)}" for value in sorted(rovers))
    return "(" + " OR ".join(clauses) + ")"


def enforce_read_scope(db, user, issue, *, token) -> bool:
    """Return whether access relies on a related, read-only robot association."""
    if is_issue_in_scope(db, user, issue):
        return False
    scope = load_related_robot_scope(db, user)
    queue = str(issue.get("queue") or "").strip().upper()
    if {park_tag_identity(tag) for tag in issue_tags(issue)} & scope.park_tags:
        enforce_issue_scope(db, user, issue)
    numbers = tuple(sorted(tracker_client.issue_robot_numbers(issue)))
    if issue.get("type_key") in ROBOT_TASK_TYPES and queue in scope.queues and numbers:
        if len(numbers) > MAX_READ_ROBOT_IDENTITIES:
            raise HTTPException(status_code=403, detail="tracker_issue_out_of_scope")
        if len(numbers) == 1:
            rows = search(db, user, token=token, robot=numbers[0], queue=queue)
        else:
            query = tracker_client.join_query(
                f"Queue: {tracker_client.ql_token(queue)}",
                "Type: repair, service, calibration",
                _compact_robot_identity_clause(issue, numbers),
                tracker_client.open_issues_clause(),
            )
            if len(query) > MAX_READ_PROOF_QUERY_LENGTH:
                raise HTTPException(status_code=403, detail="tracker_issue_out_of_scope")
            rows = _visible_rows(
                db,
                tracker_cache.search_issues(
                    token=token, query=query, filter_open=True, order=["createdAt"]
                ),
            )
        for number in numbers:
            anchors = related_robot_anchors(db, user, rows, number, scope=scope)
            if is_related_robot_issue_in_scope(db, user, issue, number, anchors, scope=scope):
                return True
    enforce_issue_scope(db, user, issue)
    return False


def search(
    db,
    user,
    *,
    token,
    robot,
    queue=None,
    park=None,
    view="open",
    include_hidden=False,
    resolved_since: datetime | None = None,
) -> list[dict]:
    number = tracker_client.normalize_robot_reference(robot)
    if number is None:
        return []
    scope = load_related_robot_scope(db, user, park)
    queues = tuple(sorted(scope.queues & {queue.strip().upper()} if queue else scope.queues))
    if not queues:
        return []
    base = tracker_client.join_query(
        "(" + " OR ".join(f"Queue: {tracker_client.ql_token(q)}" for q in queues) + ")",
        "Type: repair, service, calibration",
        tracker_client.robot_identity_clause(number),
    )

    def load(state: str) -> list[dict]:
        state_clause = {
            "open": tracker_client.open_issues_clause(),
            "closed": "Resolution: !empty()",
            "history": "Resolution: fixed",
        }[state]
        query = tracker_client.join_query(base, state_clause)
        if resolved_since is not None and state in {"closed", "history"}:
            since = resolved_since if resolved_since.tzinfo else resolved_since.replace(tzinfo=UTC)
            query_since = since.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
            query = tracker_client.join_query(
                query, f'Resolved: >= "{query_since:%Y-%m-%d %H:%M:%S}"'
            )
        return tracker_cache.search_issues(
            token=token,
            query=query,
            filter_open=state == "open",
            order=["createdAt"],
        )

    cached = tracker_cache.peek_robot_source(queues)
    rows = _visible_rows(
        db, cached if cached is not None else load(view), include_hidden=include_hidden
    )
    anchors = related_robot_anchors(db, user, rows, number, scope=scope)
    if view != "open" and cached is None and not anchors:
        open_rows = _visible_rows(db, load("open"), include_hidden=include_hidden)
        anchors = related_robot_anchors(db, user, open_rows, number, scope=scope)
    result = []
    seen = set()
    for issue in rows:
        key = str(issue.get("key") or "")
        if not key or key in seen or key.rsplit("-", 1)[0].upper() not in queues:
            continue
        if issue.get("type_key") not in ROBOT_TASK_TYPES:
            continue
        if view == "open" and not tracker_client.is_issue_open_item(issue):
            continue
        if view == "closed" and tracker_client.is_issue_open_item(issue):
            continue
        if view == "history" and issue.get("resolution_key") != "fixed":
            continue
        if resolved_since is not None and view in {"closed", "history"}:
            try:
                resolved = datetime.fromisoformat(str(issue.get("resolved") or ""))
            except ValueError:
                continue
            if resolved.tzinfo is None:
                resolved = resolved.replace(tzinfo=UTC)
            since = resolved_since if resolved_since.tzinfo else resolved_since.replace(tzinfo=UTC)
            if resolved < since:
                continue
        if not is_related_robot_issue_in_scope(db, user, issue, number, anchors, scope=scope):
            continue
        seen.add(key)
        result.append(issue)
    return sorted(result, key=lambda issue: (str(issue.get("created") or ""), str(issue["key"])))
