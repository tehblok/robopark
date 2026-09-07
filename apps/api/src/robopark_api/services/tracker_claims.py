"""Ownership checks for mechanic work sessions backed by Tracker assignees."""

from robopark_api.models import User
from robopark_api.services.rbac import RoleSlug


def mechanic_login(user: User) -> str:
    return str(user.tracker_login or user.username).strip()


def assignee_login(issue: dict) -> str:
    assignee = issue.get("assignee")
    if not isinstance(assignee, dict):
        return ""
    return str(assignee.get("login") or "").strip()


def mechanic_owns_issue(user: User, issue: dict) -> bool:
    if str(user.role) != RoleSlug.MECHANIC:
        return True
    return assignee_login(issue).casefold() == mechanic_login(user).casefold()
