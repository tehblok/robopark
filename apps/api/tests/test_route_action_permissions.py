"""HTTP permission contracts for every action advertised by route coverage.

The parametrized ids are consumed verbatim by the web route manifest.  Keep an
action here only when the request reaches the endpoint capability boundary;
404/422 is an allow result when the deliberately absent target/body is checked
after authorization, while 403 is always the deny result.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import Permission, Role, User, UserPark
from robopark_api.security import hash_password

ROLES = ("royal", "admin", "operator", "mechanic", "driver", "restricted")
BUILTIN_MANAGERS = frozenset({"royal", "admin"})
ALL_ROLES = frozenset(ROLES)

# action -> (allowed roles, permissions granted to the synthetic restricted role)
ROUTE_ACTION_CASES: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "request-park": (frozenset({"operator"}), frozenset()),
    "claim-and-transition": (
        frozenset({"mechanic"}),
        frozenset({"tracker.read", "tracker.write"}),
    ),
    "attach-photo": (
        frozenset({"royal", "admin", "operator", "mechanic", "restricted"}),
        frozenset({"tracker.read", "tracker.attach"}),
    ),
    "create-report": (ALL_ROLES, frozenset({"reports.create"})),
    "return-or-resolve": (
        frozenset({"royal", "admin", "operator", "restricted"}),
        frozenset({"reports.resolve"}),
    ),
    "hard-delete": (BUILTIN_MANAGERS, frozenset()),
    "create-campaign": (BUILTIN_MANAGERS, frozenset()),
    "update-or-delete-campaign": (BUILTIN_MANAGERS, frozenset()),
    "submit-ticket-result": (ALL_ROLES, frozenset({"reports.create"})),
    "change-platform-settings": (
        frozenset({"royal", "admin", "restricted"}),
        frozenset({"nav.admin"}),
    ),
    "backup-restore-update": (frozenset({"royal"}), frozenset()),
    "manage-user": (
        frozenset({"royal", "admin", "restricted"}),
        frozenset({"users.manage"}),
    ),
    "approve-user": (frozenset({"royal"}), frozenset()),
    "manage-role": (
        frozenset({"royal", "admin", "restricted"}),
        frozenset({"roles.manage"}),
    ),
    "map-or-ignore-error": (BUILTIN_MANAGERS, frozenset()),
    "map-diagnostic": (BUILTIN_MANAGERS, frozenset()),
    "ignore-diagnostic": (BUILTIN_MANAGERS, frozenset()),
}


def _parameters():
    for action, (allowed_roles, _permissions) in ROUTE_ACTION_CASES.items():
        for role in ROLES:
            outcome = "allow" if role in allowed_roles else "deny"
            yield pytest.param(action, role, outcome, id=f"{action}-{role}-{outcome}")


def _actor(db, park, action: str, role: str) -> User:
    if role == "restricted":
        permission_keys = ROUTE_ACTION_CASES[action][1]
        permissions = list(
            db.scalars(select(Permission).where(Permission.key.in_(permission_keys)))
        )
        custom = Role(
            slug=f"matrix_{action.replace('-', '_')}",
            name=f"Matrix {action}",
            permissions=permissions,
        )
        db.add(custom)
        db.flush()
        role_id = custom.id
    else:
        role_id = role_id_for(db, role)
    actor = User(
        username=f"matrix-{action}-{role}",
        password_hash=hash_password("secret"),
        role_id=role_id,
        access_status="approved",
        is_active=True,
    )
    db.add(actor)
    db.flush()
    db.add(UserPark(user_id=actor.id, park_id=park.id))
    db.commit()
    return actor


def _request(client, action: str, park_id: int):
    if action == "request-park":
        return client.post("/operator/park-requests", json={"park_id": park_id})
    if action == "claim-and-transition":
        return client.post("/tracker/issues/ROBOPARK-999/claim")
    if action == "attach-photo":
        return client.post(
            "/tracker/issues/ROBOPARK-999/attachments",
            files={"file": ("evidence.jpg", b"photo", "image/jpeg")},
        )
    if action == "create-report":
        return client.post(
            "/reports",
            json={
                "park_id": park_id,
                "kind": "manual",
                "title": "Matrix report",
                "body": "Permission matrix",
            },
        )
    if action == "return-or-resolve":
        return client.get(f"/reports/inbox?park_id={park_id}")
    if action == "hard-delete":
        return client.delete("/reports/999999")
    if action == "create-campaign":
        return client.post(
            "/campaigns",
            json={
                "kind": "service_company",
                "name": "Matrix campaign",
                "tracker_tag": "matrix campaign",
                "park_ids": [park_id],
                "starts_on": "2026-09-01",
                "due_on": "2026-09-30",
            },
        )
    if action == "update-or-delete-campaign":
        return client.patch("/campaigns/999999", json={"name": "Matrix"})
    if action == "submit-ticket-result":
        return client.post(
            "/campaigns/999999/tickets/ROBOPARK-999/complete",
            data={"park_id": str(park_id), "comment": "done"},
            files={"photo": ("done.jpg", b"photo", "image/jpeg")},
        )
    if action == "change-platform-settings":
        return client.put(
            "/admin/settings/tracker-policy",
            json={"queue": "ROBOPARK", "allowed_statuses": []},
        )
    if action == "backup-restore-update":
        return client.post("/admin/ops/restore")
    if action == "manage-user":
        return client.get("/admin/users")
    if action == "approve-user":
        return client.post("/admin/users/999999/approve", json={"park_ids": []})
    if action == "manage-role":
        return client.post(
            "/admin/roles",
            json={"slug": "matrix_created", "name": "Matrix", "permissions": []},
        )
    if action in {"map-or-ignore-error", "map-diagnostic"}:
        return client.post("/admin/diagnostic-rules/999999/disable")
    if action == "ignore-diagnostic":
        return client.post("/admin/diagnostic-unknowns/999999/ignore")
    raise AssertionError(f"unhandled route action: {action}")


@pytest.mark.parametrize(("action", "role", "outcome"), list(_parameters()))
def test_route_action_http_permission_matrix(
    client, db_session, seed_park_with_tracker, monkeypatch, action, role, outcome
):
    actor = _actor(db_session, seed_park_with_tracker, action, role)
    login_as(client, actor.username, "secret")

    if action in {"claim-and-transition", "attach-photo"}:
        from robopark_api.services import platform_settings, tracker_cache, tracker_client

        issue = {
            "key": "ROBOPARK-999",
            "queue": "ROBOPARK",
            "status": "Открыт",
            "tags": [seed_park_with_tracker.tag],
            "assignee": None,
        }
        monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _db: "token")
        monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: issue)
        monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: issue)
    response = _request(client, action, seed_park_with_tracker.id)

    if outcome == "deny":
        assert response.status_code == 403, response.text
    else:
        assert response.status_code not in {401, 403}, response.text


PUBLIC_AUDIENCES = ("guest", *ROLES)


@pytest.mark.parametrize(
    ("action", "audience"),
    [
        pytest.param(action, audience, id=f"{action}-{audience}-allow")
        for action in ("login", "register")
        for audience in PUBLIC_AUDIENCES
    ],
)
def test_public_auth_action_http_matrix(client, action, audience):
    """Public auth forms have no pre-existing actor; 422 proves HTTP reachability."""
    response = client.post(f"/auth/{action}", json={})
    assert response.status_code == 422, (audience, response.text)
