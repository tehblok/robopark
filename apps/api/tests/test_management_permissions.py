import pytest
from fastapi import HTTPException
from sqlalchemy import select

from conftest import VALID_PASSWORD, login_as, role_id_for
from robopark_api.models import AuthSession, Park, Permission, Role, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import emergency_scope, operations, rbac, tracker_policy


@pytest.fixture
def user_manager(db_session):
    user = User(
        username="user-manager",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "mechanic"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    rbac.set_user_effective_permissions(db_session, user, ["users.manage"])
    db_session.commit()
    return user


@pytest.fixture
def privileged_role(db_session):
    role = Role(
        slug="privileged_custom",
        name="Privileged custom",
        permissions=list(
            db_session.scalars(
                select(Permission).where(Permission.key.in_(["nav.dashboard", "roles.manage"]))
            )
        ),
    )
    db_session.add(role)
    db_session.commit()
    return role


@pytest.mark.parametrize("slug", ["admin", "privileged_custom"])
@pytest.mark.parametrize("explicit", [False, True])
def test_user_manager_cannot_create_new_privileged_account(
    client, db_session, user_manager, privileged_role, slug, explicit
):
    login_as(client, user_manager.username, "secret")
    payload = {"username": "blocked-create", "password": VALID_PASSWORD, "role_slug": slug}
    if explicit:
        payload["permissions"] = ["nav.dashboard", "nav.emergency", "tracker.read"]
    response = client.post("/admin/users", json=payload)
    assert response.status_code == 403
    assert response.json()["detail"] == "privileged_grant_forbidden"
    db_session.commit()
    assert db_session.scalar(select(User.id).where(User.username == "blocked-create")) is None


@pytest.mark.parametrize("slug", ["admin", "privileged_custom"])
@pytest.mark.parametrize("explicit", [False, True])
def test_user_manager_cannot_escalate_role_or_partially_mutate_target(
    client, db_session, user_manager, privileged_role, seed_mechanic, slug, explicit
):
    login_as(client, "mech1", "secret")
    target_id = seed_mechanic.id
    original_hash = seed_mechanic.password_hash
    original_role_id = seed_mechanic.role_id
    original_parks = list(
        db_session.scalars(select(UserPark.park_id).where(UserPark.user_id == target_id))
    )
    original_sessions = list(
        db_session.scalars(select(AuthSession.id).where(AuthSession.user_id == target_id))
    )
    login_as(client, user_manager.username, "secret")
    payload = {
        "role_slug": slug,
        "password": VALID_PASSWORD,
        "park_ids": [],
        "tracker_login": "must-not-persist",
        "is_active": False,
    }
    if explicit:
        payload["permissions"] = ["nav.dashboard", "nav.emergency", "tracker.read"]
    response = client.patch(f"/admin/users/{target_id}", json=payload)
    assert response.status_code == 403
    # The dependency shares this session: rejection must precede mutation,
    # not merely rely on a request-scoped rollback in production.
    db_session.commit()
    db_session.refresh(seed_mechanic)
    assert seed_mechanic.role_id == original_role_id
    assert seed_mechanic.password_hash == original_hash
    assert seed_mechanic.tracker_login is None
    assert seed_mechanic.is_active is True
    assert (
        list(db_session.scalars(select(UserPark.park_id).where(UserPark.user_id == target_id)))
        == original_parks
    )
    assert (
        list(db_session.scalars(select(AuthSession.id).where(AuthSession.user_id == target_id)))
        == original_sessions
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"permissions": ["nav.dashboard", "nav.admin"]},
        {"role_slug": "admin"},
    ],
)
def test_user_manager_cannot_restore_revoked_privileged_role_permission(
    client, db_session, user_manager, payload
):
    target = User(
        username="restricted-admin",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "admin"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(target)
    db_session.flush()
    rbac.set_user_effective_permissions(db_session, target, ["nav.dashboard"])
    db_session.commit()
    login_as(client, user_manager.username, "secret")
    response = client.patch(f"/admin/users/{target.id}", json=payload)
    assert response.status_code == 403
    db_session.commit()
    assert rbac.permissions_for_user(db_session, target) == {"nav.dashboard"}


@pytest.mark.parametrize("slug", ["admin", "privileged_custom"])
def test_owner_can_create_and_assign_privileged_roles(
    client, db_session, seed_royal, seed_mechanic, privileged_role, slug
):
    login_as(client, "royal", "secret")
    created = client.post(
        "/admin/users",
        json={
            "username": "owner-created",
            "password": VALID_PASSWORD,
            "role_slug": slug,
        },
    )
    assert created.status_code == 201
    assert "roles.manage" in created.json()["permissions"]
    updated = client.patch(f"/admin/users/{seed_mechanic.id}", json={"role_slug": slug})
    assert updated.status_code == 200
    assert updated.json()["role"] == slug
    assert "roles.manage" in updated.json()["permissions"]


def test_user_manager_can_assign_nonprivileged_effective_permissions(
    client, user_manager, seed_mechanic
):
    login_as(client, user_manager.username, "secret")
    created = client.post(
        "/admin/users",
        json={
            "username": "ordinary-created",
            "password": VALID_PASSWORD,
            "role_slug": "driver",
        },
    )
    assert created.status_code == 201
    updated = client.patch(f"/admin/users/{seed_mechanic.id}", json={"role_slug": "operator"})
    assert updated.status_code == 200
    assert updated.json()["role"] == "operator"
    assert "reports.resolve" in updated.json()["permissions"]


def test_rejected_admin_identity_preserves_operations_tracker_and_vin_park_scope(
    client, db_session, user_manager, seed_mechanic, monkeypatch
):
    foreign = Park(
        name="Foreign",
        tag="Foreign",
        is_active=True,
        tracker_queue="FOREIGN",
        feature_blockers=True,
    )
    db_session.add(foreign)
    db_session.commit()
    original_role_id = seed_mechanic.role_id

    login_as(client, user_manager.username, "secret")
    response = client.patch(
        f"/admin/users/{seed_mechanic.id}",
        json={
            "role_slug": "admin",
            "permissions": ["nav.dashboard", "nav.emergency", "tracker.read"],
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "privileged_grant_forbidden"
    db_session.commit()
    db_session.refresh(seed_mechanic)
    assert seed_mechanic.role_id == original_role_id

    with pytest.raises(HTTPException) as operations_denied:
        operations.require_operations_park(db_session, seed_mechanic, foreign.id)
    assert operations_denied.value.status_code == 403

    foreign_issue = {
        "key": "FOREIGN-1",
        "queue": "FOREIGN",
        "summary": "robot 447",
        "status": "Open",
        "status_key": "open",
        "tags": ["Foreign"],
    }
    with pytest.raises(HTTPException) as tracker_denied:
        tracker_policy.enforce_issue_scope(db_session, seed_mechanic, foreign_issue)
    assert tracker_denied.value.status_code == 403

    monkeypatch.setattr(
        emergency_scope.settings_svc,
        "get_tracker_token",
        lambda _db: "fixture-token",
    )
    monkeypatch.setattr(
        emergency_scope.tracker_cache,
        "search_robot_tickets",
        lambda **_kwargs: [foreign_issue],
    )
    assert (
        emergency_scope.vin_allowed_for_user(db_session, seed_mechanic, "YASADR00000000447")
        is False
    )


@pytest.mark.parametrize("capability", ["users.manage", "roles.manage"])
def test_management_catalog_reads_use_management_capabilities(
    client, db_session, user_manager, capability
):
    rbac.set_user_effective_permissions(db_session, user_manager, [capability])
    db_session.commit()
    login_as(client, user_manager.username, "secret")
    assert client.get("/admin/roles").status_code == 200
    assert client.get("/admin/roles/permissions/catalog").status_code == 200


def test_user_manager_catalog_access_does_not_allow_role_mutations(
    client, user_manager, privileged_role
):
    login_as(client, user_manager.username, "secret")
    assert client.post("/admin/roles", json={"slug": "new_role", "name": "New"}).status_code == 403
    assert (
        client.patch(f"/admin/roles/{privileged_role.id}", json={"name": "Changed"}).status_code
        == 403
    )
    assert client.delete(f"/admin/roles/{privileged_role.id}").status_code == 403


@pytest.mark.parametrize("capability", ["nav.admin", "nav.dashboard"])
def test_catalog_denies_unrelated_permissions(client, db_session, user_manager, capability):
    rbac.set_user_effective_permissions(db_session, user_manager, [capability])
    db_session.commit()
    login_as(client, user_manager.username, "secret")
    assert client.get("/admin/roles").status_code == 403
    assert client.get("/admin/roles/permissions/catalog").status_code == 403


@pytest.mark.parametrize("access", ["pending", "rejected"])
def test_last_approved_owner_cannot_lose_approval(client, seed_royal, access):
    login_as(client, "royal", "secret")
    response = client.patch(f"/admin/users/{seed_royal.id}", json={"access_status": access})
    assert response.status_code == 400


def test_unapproved_owner_does_not_make_last_approved_owner_expendable(
    client, db_session, seed_royal
):
    db_session.add(
        User(
            username="pending-owner",
            password_hash=hash_password("secret"),
            role_id=seed_royal.role_id,
            access_status="pending",
            is_active=True,
        )
    )
    db_session.commit()
    login_as(client, "royal", "secret")
    assert (
        client.patch(f"/admin/users/{seed_royal.id}", json={"is_active": False}).status_code == 400
    )


def test_owner_cannot_reject_last_approved_owner(client, seed_royal):
    login_as(client, "royal", "secret")
    assert client.post(f"/admin/users/{seed_royal.id}/reject").status_code == 400


@pytest.mark.parametrize("capability", ["users.manage", "parks.manage", "nav.admin"])
def test_granular_park_catalog_read(client, db_session, user_manager, capability):
    rbac.set_user_effective_permissions(db_session, user_manager, [capability])
    db_session.commit()
    login_as(client, user_manager.username, "secret")
    assert client.get("/parks").status_code == 200


def test_park_manager_can_mutate_without_admin_navigation(
    client, db_session, user_manager, seed_park_with_tracker
):
    rbac.set_user_effective_permissions(db_session, user_manager, ["parks.manage"])
    db_session.commit()
    login_as(client, user_manager.username, "secret")
    assert client.post("/parks", json={"name": "New", "tag": "New"}).status_code == 201
    response = client.patch(f"/parks/{seed_park_with_tracker.id}", json={"name": "Renamed"})
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


@pytest.mark.parametrize("capability", ["users.manage", "nav.admin"])
def test_park_read_capability_does_not_grant_mutations(
    client, db_session, user_manager, seed_park_with_tracker, capability
):
    rbac.set_user_effective_permissions(db_session, user_manager, [capability])
    db_session.commit()
    login_as(client, user_manager.username, "secret")
    assert client.post("/parks", json={"name": "New", "tag": "New"}).status_code == 403
    assert (
        client.patch(f"/parks/{seed_park_with_tracker.id}", json={"name": "No"}).status_code == 403
    )


@pytest.mark.parametrize("capability", ["users.manage", "parks.manage", "nav.admin"])
def test_unapproved_granular_park_access_denied(
    client, db_session, user_manager, seed_park_with_tracker, capability
):
    rbac.set_user_effective_permissions(db_session, user_manager, [capability])
    user_manager.access_status = "pending"
    db_session.commit()
    login_as(client, user_manager.username, "secret")
    assert client.get("/parks").status_code == 403
    assert client.post("/parks", json={"name": "New", "tag": "New"}).status_code == 403
    assert (
        client.patch(f"/parks/{seed_park_with_tracker.id}", json={"name": "No"}).status_code == 403
    )
