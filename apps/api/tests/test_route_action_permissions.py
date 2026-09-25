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
from robopark_api.models import AccessStatus, Park, Permission, Report, Role, User, UserPark
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
        frozenset({"royal", "admin", "operator"}),
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


ACTION_HTTP_CONTRACTS = {
    "request-park": ("POST", "/operator/park-requests", {201}),
    "claim-and-transition": ("POST", "/tracker/issues/ROBOPARK-999/claim", {200, 409}),
    "attach-photo": ("POST", "/tracker/issues/ROBOPARK-999/attachments", {200}),
    "create-report": ("POST", "/reports", {201}),
    "return-or-resolve": ("POST", "/reports/{report_id}/return", {200}),
    "hard-delete": ("DELETE", "/reports/{report_id}", {204}),
    "create-campaign": ("POST", "/campaigns", {201}),
    "update-or-delete-campaign": ("PATCH", "/campaigns/999999", {404}),
    "submit-ticket-result": ("POST", "/campaigns/999999/tickets/ROBOPARK-999/complete", {404}),
    "change-platform-settings": ("PUT", "/admin/settings/tracker-policy", {200}),
    "manage-user": ("PATCH", "/admin/users/{actor_id}", {200}),
    "approve-user": ("POST", "/admin/users/{pending_user_id}/approve", {204}),
    "manage-role": ("POST", "/admin/roles", {201}),
    "map-or-ignore-error": ("POST", "/admin/diagnostic-rules/999999/disable", {404}),
    "map-diagnostic": ("POST", "/admin/diagnostic-rules/999999/disable", {404}),
    "ignore-diagnostic": ("POST", "/admin/diagnostic-unknowns/999999/ignore", {404}),
}


def _seed_targets(db, park, actor: User) -> dict[str, int]:
    request_park = Park(
        name=f"Matrix request {actor.username}",
        tag=f"matrix-request-{actor.id}",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_blockers=True,
    )
    author = User(
        username=f"author-for-{actor.username}",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, "mechanic"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    pending = User(
        username=f"pending-for-{actor.username}",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, "operator"),
        access_status=AccessStatus.pending.value,
        is_active=True,
    )
    db.add_all([request_park, author, pending])
    db.flush()
    report = Report(
        author_user_id=author.id,
        park_id=park.id,
        kind="mechanic_problem",
        status="open",
        target_role="operator",
        title="Matrix target",
        body="Mutable fixture",
    )
    db.add(report)
    db.commit()
    return {
        "report_id": report.id,
        "pending_user_id": pending.id,
        "request_park_id": request_park.id,
    }


def _request(client, action: str, park_id: int, *, actor_id: int, targets: dict[str, int]):
    if action == "request-park":
        return client.post("/operator/park-requests", json={"park_id": targets["request_park_id"]})
    if action == "claim-and-transition":
        return client.post(
            "/tracker/issues/ROBOPARK-999/claim",
            headers={"Idempotency-Key": "route-matrix-claim-999"},
        )
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
                "kind": "mechanic_problem",
                "title": "Matrix report",
                "body": "Permission matrix",
            },
        )
    if action == "return-or-resolve":
        return client.post(
            f"/reports/{targets['report_id']}/return",
            json={"comment": "Matrix resolution"},
        )
    if action == "hard-delete":
        return client.delete(f"/reports/{targets['report_id']}")
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
    if action == "manage-user":
        return client.patch(f"/admin/users/{actor_id}", json={"tracker_login": "matrix.updated"})
    if action == "approve-user":
        return client.post(
            f"/admin/users/{targets['pending_user_id']}/approve", json={"park_ids": []}
        )
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
    targets = _seed_targets(db_session, seed_park_with_tracker, actor)
    login_as(client, actor.username, "secret")

    if action in {"claim-and-transition", "attach-photo"}:
        from robopark_api.services import platform_settings, tracker_cache, tracker_client

        issue = {
            "key": "ROBOPARK-999",
            "summary": "Matrix issue [447]",
            "queue": "ROBOPARK",
            "status": "Открыт",
            "status_key": "open",
            "tags": [seed_park_with_tracker.tag],
            "assignee": None,
        }
        monkeypatch.setattr(platform_settings, "get_tracker_token", lambda _db: "token")
        monkeypatch.setattr(tracker_client, "get_issue", lambda **_kwargs: issue)
        monkeypatch.setattr(tracker_cache, "get_issue", lambda **_kwargs: issue)
        monkeypatch.setattr(
            tracker_client, "upload_temp_attachment", lambda **_kwargs: "matrix-upload"
        )
        monkeypatch.setattr(tracker_client, "add_comment", lambda **_kwargs: None)
        if action == "attach-photo" and role == "mechanic":
            from robopark_api.services.tracker_claims import claim_issue

            claim_issue(
                db_session,
                actor=actor,
                owner=actor,
                issue_key="ROBOPARK-999",
                park_id=seed_park_with_tracker.id,
            )
            db_session.commit()
    response = _request(
        client,
        action,
        seed_park_with_tracker.id,
        actor_id=actor.id,
        targets=targets,
    )
    method, path_template, _success_statuses = ACTION_HTTP_CONTRACTS[action]
    expected_path = path_template.format(actor_id=actor.id, **targets)
    assert response.request.method == method, action
    assert response.request.url.path == expected_path, action
    if action == "return-or-resolve":
        assert response.request.read() == b'{"comment":"Matrix resolution"}'
    if action == "manage-user":
        assert response.request.read() == b'{"tracker_login":"matrix.updated"}'

    if outcome == "deny":
        assert response.status_code in {401, 403}, response.text
    else:
        expected = _success_statuses
        assert response.status_code in expected, response.text


def test_every_route_action_names_its_exact_http_mutation_contract():
    assert set(ACTION_HTTP_CONTRACTS) == set(ROUTE_ACTION_CASES)
    for action, (method, path, success_statuses) in ACTION_HTTP_CONTRACTS.items():
        assert method in {"POST", "PUT", "PATCH", "DELETE"}, action
        assert path.startswith("/"), action
        assert success_statuses and all(status < 500 for status in success_statuses), action
    assert ACTION_HTTP_CONTRACTS["return-or-resolve"][:2] == (
        "POST",
        "/reports/{report_id}/return",
    )
    assert ACTION_HTTP_CONTRACTS["manage-user"][:2] == (
        "PATCH",
        "/admin/users/{actor_id}",
    )


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
