from unittest.mock import Mock

from sqlalchemy import select

from conftest import login_as
from robopark_api.models import AccessStatus, Park, Permission, Role, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import emergency_scope, platform_settings, rbac


def _issue(key: str, tag: str) -> dict:
    return {
        "key": key,
        "summary": "blocker [447]",
        "status": "Open",
        "status_key": "open",
        "queue": "ROBOPARK",
        "created": "2026-01-01T00:00:00Z",
        "hours_created": "1",
        "tags": [tag],
        "robot": "447",
    }


def _seed_field_lead(db_session, *, assign_alpha: bool = True):
    alpha = Park(name="Alpha", tag="Alpha", is_active=True, tracker_queue="ROBOPARK")
    beta = Park(name="Beta", tag="Beta", is_active=True, tracker_queue="ROBOPARK")
    db_session.add_all([alpha, beta])
    db_session.flush()

    keys = {
        rbac.PERMISSION_NAV_DASHBOARD,
        rbac.PERMISSION_NAV_TASKS,
        rbac.PERMISSION_NAV_ROBOT_SEARCH,
        rbac.PERMISSION_NAV_EMERGENCY,
        rbac.PERMISSION_TRACKER_READ,
        rbac.PERMISSION_TRACKER_WRITE,
    }
    permissions = list(db_session.scalars(select(Permission).where(Permission.key.in_(keys))))
    assert {permission.key for permission in permissions} == keys
    role = Role(
        slug="field_lead",
        name="Старший площадки",
        description="Операционная роль",
        is_system=False,
        is_active=True,
        permissions=permissions,
    )
    db_session.add(role)
    db_session.flush()
    user = User(
        username="field-lead",
        password_hash=hash_password("secret"),
        role_id=role.id,
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    if assign_alpha:
        db_session.add(UserPark(user_id=user.id, park_id=alpha.id))
    db_session.commit()
    db_session.refresh(user)
    return user, alpha, beta


def test_custom_operational_role_uses_only_its_assigned_park(client, db_session, monkeypatch):
    user, alpha, beta = _seed_field_lead(db_session)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_cache, tracker_client

    monkeypatch.setattr(
        tracker_cache,
        "collect_park_metrics",
        lambda **_kwargs: {"arrived": 1, "done": 2, "queued": 3, "in_transit": 4},
    )
    monkeypatch.setattr(tracker_client, "fetch_park_blockers", lambda **_kwargs: [])
    monkeypatch.setattr(
        tracker_client,
        "search_issues",
        lambda **_kwargs: [_issue("ROBOPARK-42", "Alpha"), _issue("ROBOPARK-99", "Beta")],
    )
    monkeypatch.setattr(
        tracker_cache,
        "search_robot_tickets",
        lambda **_kwargs: [_issue("ROBOPARK-42", "Alpha")],
    )
    login_as(client, user.username, "secret")

    own_summary = client.get(f"/dashboard/summary?park_id={alpha.id}")
    foreign_summary = client.get(f"/dashboard/summary?park_id={beta.id}")
    issues = client.get("/tracker/issues?queue=ROBOPARK&park=Alpha")

    assert own_summary.status_code == 200
    assert foreign_summary.status_code == 403
    assert issues.status_code == 200
    assert [item["key"] for item in issues.json()["items"]] == ["ROBOPARK-42"]
    assert emergency_scope.vin_allowed_for_user(db_session, user, "YASADR00000000447") is True


def test_custom_role_without_backend_capability_is_denied(client, db_session, monkeypatch):
    user, alpha, _beta = _seed_field_lead(db_session)
    rbac.set_user_effective_permissions(db_session, user, [rbac.PERMISSION_NAV_TASKS])
    db_session.commit()
    login_as(client, user.username, "secret")

    assert client.get(f"/dashboard/summary?park_id={alpha.id}").status_code == 403
    assert client.get("/tracker/issues").status_code == 403


def test_custom_tracker_role_without_assigned_parks_never_runs_upstream_search(
    client, db_session, monkeypatch
):
    user, _alpha, _beta = _seed_field_lead(db_session, assign_alpha=False)
    platform_settings.set_setting(db_session, platform_settings.TRACKER_TOKEN_KEY, "token")

    from robopark_api.services import tracker_client

    search_issues = Mock(return_value=[])
    monkeypatch.setattr(tracker_client, "search_issues", search_issues)
    login_as(client, user.username, "secret")

    response = client.get("/tracker/issues")

    assert response.status_code == 403
    assert response.json()["detail"] == "tracker_scope_empty"
    search_issues.assert_not_called()
