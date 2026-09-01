"""Signed Tracker comment lines for platform accountability."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from robopark_api.models import AccessStatus, Park, Role, User, UserPark
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.tracker_policy import issue_tags

# Текст комментария
# Next / mech.login / operator.login
PLATFORM_SIGNATURE_FOOTER_RE = re.compile(
    r"\n(?P<park>[^/\n]+) / (?P<mechanic>[^/\n]+) / (?P<operator>[^\n]+)\s*$",
)
PLATFORM_COMMENT_LEGACY_RE = re.compile(
    r"^\[\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}\] – .+:.+",
    re.MULTILINE,
)
LEGACY_PLATFORM_FOOTER = "\n\n—\nРобопарк:"
MISSING = "—"


@dataclass(frozen=True)
class SignatureContext:
    park_id: int | None
    park_name: str
    mechanic_login: str
    operator_login: str


def is_platform_signed_comment(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    if PLATFORM_SIGNATURE_FOOTER_RE.search(normalized):
        return True
    if PLATFORM_COMMENT_LEGACY_RE.search(normalized):
        return True
    return LEGACY_PLATFORM_FOOTER in normalized


def resolve_park(db: Session, issue: dict) -> Park | None:
    tags = issue_tags(issue)
    if tags:
        park = db.scalar(select(Park).where(Park.tag.in_(tags)).limit(1))
        if park is not None:
            return park

    queue = str(issue.get("queue") or "").strip()
    if queue:
        return db.scalar(select(Park).where(Park.tracker_queue == queue).limit(1))
    return None


def _assignee_login(issue: dict) -> str | None:
    assignee = issue.get("assignee")
    if not isinstance(assignee, dict):
        return None
    login = str(assignee.get("login") or "").strip()
    return login or None


def resolve_mechanic_login(db: Session, issue: dict, user: User) -> str:
    assignee_login = _assignee_login(issue)
    if assignee_login:
        matched = db.scalar(
            select(User)
            .where((User.tracker_login == assignee_login) | (User.username == assignee_login))
            .limit(1)
        )
        if matched is not None:
            return (matched.tracker_login or matched.username).strip()
        return assignee_login

    if user.role == RoleSlug.MECHANIC:
        return (user.tracker_login or user.username).strip()

    return MISSING


def resolve_operator_login(db: Session, park: Park | None, user: User) -> str:
    if park is None:
        return user.username if user.role == RoleSlug.OPERATOR else MISSING

    if user.role == RoleSlug.OPERATOR:
        assigned = db.scalar(
            select(UserPark.user_id).where(
                UserPark.user_id == user.id,
                UserPark.park_id == park.id,
            )
        )
        if assigned is not None:
            return user.username

    operator = db.scalar(
        select(User)
        .join(UserPark, UserPark.user_id == User.id)
        .join(Role)
        .where(
            UserPark.park_id == park.id,
            Role.slug == RoleSlug.OPERATOR,
            User.access_status == AccessStatus.approved.value,
            User.is_active.is_(True),
        )
        .order_by(User.username)
        .limit(1)
    )
    if operator is not None:
        return operator.username

    return MISSING


def build_signature_context(db: Session, user: User, issue: dict) -> SignatureContext:
    park = resolve_park(db, issue)
    return SignatureContext(
        park_id=park.id if park is not None else None,
        park_name=park.name if park is not None else MISSING,
        mechanic_login=resolve_mechanic_login(db, issue, user),
        operator_login=resolve_operator_login(db, park, user),
    )


def format_signed_comment(
    *,
    body: str,
    park_name: str,
    mechanic_login: str,
    operator_login: str,
) -> str:
    footer = f"{park_name} / {mechanic_login} / {operator_login}"
    return f"{body.strip()}\n{footer}"


STAFF_ROLES = frozenset({RoleSlug.OPERATOR, RoleSlug.ADMIN, RoleSlug.ROYAL})


def staff_tracker_logins(db: Session) -> set[str]:
    """Usernames and tracker logins of platform staff visible to mechanics."""
    rows = db.scalars(
        select(User)
        .join(Role)
        .where(
            Role.slug.in_(STAFF_ROLES),
            User.is_active.is_(True),
        )
    ).all()
    out: set[str] = set()
    for user in rows:
        username = str(user.username or "").strip()
        if username:
            out.add(username)
        tracker_login = str(user.tracker_login or "").strip()
        if tracker_login:
            out.add(tracker_login)
    return out


def filter_mechanic_visible_comments(db: Session, comments: list[dict]) -> list[dict]:
    """Platform-signed comments plus staff (operator/admin) comments."""
    staff = staff_tracker_logins(db)
    out: list[dict] = []
    for item in comments:
        text = str(item.get("text") or "")
        if is_platform_signed_comment(text):
            out.append(item)
            continue
        login = str(item.get("author_login") or "").strip()
        if login and login in staff:
            out.append(item)
    return out


def filter_platform_comments(comments: list[dict]) -> list[dict]:
    return [item for item in comments if is_platform_signed_comment(str(item.get("text") or ""))]
