"""Park-scope ACL for Emergency VIN access.

Emergency exposes live diagnostics for *any* robot to anyone with the shared
cookie. To keep operator/mechanic access matched to what they can already see
in Tracker, we only let a non-admin user open a VIN when the same robot has a
Tracker ticket in one of their parks. Admins/royals are unrestricted.

This is a fail-closed check: if Tracker has no token, no matching queue, or
no ticket, the VIN is denied. Any Tracker call errors are propagated so the
router can turn them into a 502 rather than a silent 403.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from robopark_api.deps import get_user_parks
from robopark_api.models import User
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services import rbac
from robopark_api.services import tracker_cache, tracker_client
from robopark_api.services.rbac import RoleSlug
from robopark_api.services.tracker_policy import is_issue_in_scope


def robot_query_from_vin(vin: str) -> str:
    """Convert a VIN like ``YASADR00000000447`` into the short number ``447``.

    Tracker tickets reference the short robot number in the summary, so the
    search query must strip the ``YASADR`` prefix and any leading zeros.
    """
    text = (vin or "").strip().upper()
    if text.startswith("YASADR"):
        text = text[6:]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.lstrip("0") or "0"


def vin_allowed_for_user(db: Session, user: User, vin: str) -> bool:
    """True when *user* may read Emergency diagnostics for *vin*.

    Product note (intentional): approved drivers may open any VIN without a
    Tracker park/ticket check — the driver cabinet is Emergency-only and does
    not assign parks. Admins/royals are also unrestricted. Operators and
    mechanics require a matching in-scope Tracker ticket.
    """
    if rbac.is_admin_or_royal(user):
        return True
    if rbac.role_slug(user) == RoleSlug.DRIVER:
        return True

    parks = [park for park in get_user_parks(db, user) if (park.tracker_queue or "").strip()]
    if not parks:
        return False

    token = settings_svc.get_tracker_token(db)
    if not token:
        return False

    query = robot_query_from_vin(vin)
    seen_queues: set[str] = set()
    for park in parks:
        queue = (park.tracker_queue or "").strip()
        if not queue or queue in seen_queues:
            continue
        seen_queues.add(queue)
        issues = tracker_cache.search_robot_tickets(token=token, queue=queue, query=query)
        if any(is_issue_in_scope(db, user, issue) for issue in issues):
            return True
    return False
