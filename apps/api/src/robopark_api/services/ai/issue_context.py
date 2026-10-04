"""Reuse actual Tracker access rules before adding a ticket to a prompt."""

import json

from fastapi import HTTPException
from sqlalchemy import select

from robopark_api.models import Park
from robopark_api.services import platform_settings, task_lifecycle, tracker_cache, tracker_client
from robopark_api.services.ai import knowledge
from robopark_api.services.tracker_claims import mechanic_can_access_issue
from robopark_api.services.tracker_policy import enforce_issue_scope, issue_tags, park_tag_matches
from robopark_api.task_workflow_models import TaskMessage


def fetch(token, issue_key):
    if not issue_key:
        return None
    if not token:
        raise HTTPException(503, "ai_issue_unavailable")
    try:
        issue = tracker_cache.get_issue(token=token, key=issue_key)
    except tracker_client.TrackerError:
        raise HTTPException(503, "ai_issue_unavailable") from None
    if not issue:
        raise HTTPException(404, "ai_issue_unavailable")
    return issue


def authorize_snapshot(db, user, issue, issue_key, park_id):
    if not issue_key:
        if issue is not None:
            raise HTTPException(404, "ai_issue_unavailable")
        return None
    if task_lifecycle.is_hidden(db, issue_key):
        raise HTTPException(404, "ai_issue_unavailable")
    if not isinstance(issue, dict) or str(issue.get("key") or "") != issue_key:
        raise HTTPException(404, "ai_issue_unavailable")
    enforce_issue_scope(db, user, issue)
    if not mechanic_can_access_issue(db, user, issue):
        raise HTTPException(403, "ai_issue_unavailable")
    park = db.get(Park, park_id)
    if (
        park is None
        or str(issue.get("queue", "")) != park.tracker_queue
        or not park_tag_matches(park.tag, issue_tags(issue))
    ):
        raise HTTPException(403, "ai_issue_park_mismatch")
    return issue


def load(db, user, issue_key, park_id):
    if not issue_key:
        return None
    if task_lifecycle.is_hidden(db, issue_key):
        raise HTTPException(404, "ai_issue_unavailable")
    token = platform_settings.get_tracker_token(db)
    return authorize_snapshot(db, user, fetch(token, issue_key), issue_key, park_id)


def context(db, user, issue):
    if issue is None:
        return ""
    data = {
        key: issue.get(key)
        for key in (
            "key",
            "summary",
            "description",
            "status",
            "components",
            "component_ids",
            "defect_code",
            "solution_method",
        )
    }
    data["description"] = str(data.get("description") or "")[:2500]
    # Only locally authored, non-private participant messages. Tracker comment
    # visibility is separate, so arbitrary cached upstream comments stay out.
    comments = db.scalars(
        select(TaskMessage)
        .where(
            TaskMessage.issue_key == issue["key"],
            TaskMessage.kind == "user",
            TaskMessage.visibility == "participants",
        )
        .order_by(TaskMessage.created_at.desc())
        .limit(3)
    )
    data["local_comments"] = [row.text[:500] for row in comments][::-1]
    return knowledge.redact(json.dumps(data, ensure_ascii=False))[:4500]
