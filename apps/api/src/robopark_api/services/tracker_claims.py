"""Local task ownership; upstream Tracker assignment is intentionally ignored."""

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.collaboration_models import TrackerClaim
from robopark_api.models import User


def mechanic_login(user: User) -> str:
    """Compatibility display value; work never requires a Tracker login."""
    return str(user.username).strip()


def assignee_login(issue: dict) -> str:
    assignee = issue.get("assignee")
    if not isinstance(assignee, dict):
        return ""
    return str(assignee.get("login") or "").strip()


def _key(issue: dict) -> str:
    return str(issue.get("key") or "").strip()


def get_claim(db: Session, issue_key: str) -> TrackerClaim | None:
    return db.get(TrackerClaim, issue_key.strip())


def claim_issue(
    db: Session,
    *,
    actor: User,
    owner: User,
    issue_key: str,
    park_id: int,
    replace: bool = False,
) -> TrackerClaim:
    key = issue_key.strip()
    current = get_claim(db, key)
    if current is not None:
        if current.owner_user_id == owner.id:
            return current
        if not replace:
            raise PermissionError("tracker_issue_already_claimed")
        current.owner_user_id = owner.id
        current.updated_by_user_id = actor.id
        current.park_id = park_id
        current.updated_at = time.time()
        db.flush()
        return current
    current = TrackerClaim(
        issue_key=key,
        park_id=park_id,
        owner_user_id=owner.id,
        updated_by_user_id=actor.id,
        updated_at=time.time(),
    )
    db.add(current)
    db.flush()
    return current


def release_claim(db: Session, issue_key: str) -> None:
    current = get_claim(db, issue_key)
    if current is not None:
        db.delete(current)
        db.flush()


def mechanic_owns_issue(db: Session, user: User, issue: dict) -> bool:
    claim = get_claim(db, _key(issue))
    return claim is not None and claim.owner_user_id == user.id


def mechanic_can_access_issue(db: Session, user: User, issue: dict) -> bool:
    # Mechanics must be able to inspect a shiftmate's task before explicitly
    # taking responsibility for it. Mutations other than ``assign`` still use
    # ``mechanic_owns_issue`` and remain blocked until that takeover.
    return True


def local_assignee(db: Session, issue: dict) -> dict[str, str] | None:
    claim = get_claim(db, _key(issue))
    if claim is None:
        return None
    owner = db.get(User, claim.owner_user_id)
    if owner is None or not owner.is_active:
        return None
    return {"login": owner.username, "display": owner.username}


def local_assignees(db: Session, issue_keys: list[str]) -> dict[str, dict[str, str]]:
    keys = {key.strip() for key in issue_keys if key.strip()}
    if not keys:
        return {}
    rows = db.execute(
        select(TrackerClaim.issue_key, User.username)
        .join(User, User.id == TrackerClaim.owner_user_id)
        .where(TrackerClaim.issue_key.in_(keys), User.is_active.is_(True))
    ).all()
    return {key: {"login": username, "display": username} for key, username in rows}
