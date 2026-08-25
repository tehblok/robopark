"""Assignee suggestions for Tracker issue actions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.deps import get_mechanic_park, get_user_parks
from robopark_api.models import User, UserPark, UserRole


def list_assignee_candidates(db: Session, user: User, query: str, *, limit: int = 20) -> list[dict[str, str]]:
    """Mechanics with a Startrek login in parks visible to the current user."""
    needle = query.strip().lower()
    if not needle:
        return []

    park_ids: list[int] | None
    if user.role in {UserRole.admin.value, UserRole.royal.value}:
        park_ids = None
    elif user.role == UserRole.operator.value:
        park_ids = [park.id for park in get_user_parks(db, user)]
        if not park_ids:
            return []
    elif user.role == UserRole.mechanic.value:
        park = get_mechanic_park(db, user)
        park_ids = [park.id] if park else []
        if not park_ids:
            return []
    else:
        return []

    stmt = (
        select(User)
        .join(UserPark, UserPark.user_id == User.id)
        .where(
            User.role == UserRole.mechanic.value,
            User.is_active.is_(True),
            User.tracker_login.is_not(None),
            User.tracker_login != "",
        )
    )
    if park_ids is not None:
        stmt = stmt.where(UserPark.park_id.in_(park_ids))

    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for row in db.scalars(stmt).unique():
        login = (row.tracker_login or "").strip()
        if not login:
            continue
        if needle not in login.lower() and needle not in row.username.lower():
            continue
        key = login.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append({"login": login, "display": login, "source": "park"})
        if len(results) >= limit:
            break
    return results
