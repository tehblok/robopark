import pytest
from sqlalchemy import delete, select

from conftest import login_as, role_id_for
from robopark_api.models import Park, Report, ReportAttachment, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import rbac
from robopark_api.services import reports as reports_svc


def _user(db, slug, park=None, *, name=None, access="approved"):
    user = User(
        username=name or f"report-{slug}",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, slug),
        access_status=access,
        is_active=True,
    )
    db.add(user)
    db.flush()
    if park:
        db.add(UserPark(user_id=user.id, park_id=park.id))
    db.commit()
    return user


def _report(db, author, park, *, target="operator", state="open"):
    row = Report(
        author_user_id=author.id,
        park_id=park.id if park is not None else None,
        kind="mechanic_problem",
        status=state,
        target_role=target,
        title="Report",
        body="Body",
    )
    db.add(row)
    db.commit()
    return row


def _payload(park_id):
    return {"park_id": park_id, "kind": "mechanic_problem", "title": "Report", "body": "Body"}


@pytest.mark.parametrize("slug", ["mechanic", "driver", "operator", "admin", "royal"])
def test_every_approved_standard_role_can_create_in_authorized_park(
    client, db_session, seed_park_with_tracker, slug
):
    park = seed_park_with_tracker
    user = _user(db_session, slug, None if slug in {"admin", "royal"} else park)
    login_as(client, user.username, "secret")
    response = client.post("/reports", json=_payload(park.id))
    assert response.status_code == 201
    assert response.json()["author_user_id"] == user.id
    assert response.json()["target_role"] == "operator"
    assert "nav.reports" in client.get("/auth/me").json()["permissions"]


@pytest.mark.parametrize("slug", ["driver", "operator", "admin"])
def test_explicit_create_deny_is_respected(client, db_session, seed_park_with_tracker, slug):
    user = _user(db_session, slug, seed_park_with_tracker)
    rbac.set_user_effective_permissions(db_session, user, ["nav.reports"])
    db_session.commit()
    login_as(client, user.username, "secret")
    assert client.post("/reports", json=_payload(seed_park_with_tracker.id)).status_code == 403
    assert db_session.scalar(select(Report.id)) is None


@pytest.mark.parametrize("slug", ["mechanic", "driver", "operator", "admin", "royal"])
def test_unapproved_role_cannot_create_report(client, db_session, seed_park_with_tracker, slug):
    user = _user(db_session, slug, seed_park_with_tracker, access="pending")
    login_as(client, user.username, "secret")
    assert client.post("/reports", json=_payload(seed_park_with_tracker.id)).status_code == 403


@pytest.mark.parametrize("slug", ["mechanic", "driver", "operator"])
def test_report_create_rejects_unassigned_park(client, db_session, seed_park_with_tracker, slug):
    user = _user(db_session, slug)
    login_as(client, user.username, "secret")
    assert client.post("/reports", json=_payload(seed_park_with_tracker.id)).status_code == 403


@pytest.mark.parametrize("slug", ["mechanic", "operator", "admin", "royal"])
def test_report_create_rejects_inactive_park(client, db_session, seed_park_with_tracker, slug):
    user = _user(db_session, slug, seed_park_with_tracker)
    seed_park_with_tracker.is_active = False
    db_session.commit()
    login_as(client, user.username, "secret")
    assert client.post("/reports", json=_payload(seed_park_with_tracker.id)).status_code == 403


@pytest.mark.parametrize("slug,target", [("operator", "operator"), ("admin", "admin")])
def test_revoked_resolver_cannot_view_or_process_other_reports(
    client, db_session, seed_mechanic, seed_park_with_tracker, slug, target
):
    user = _user(db_session, slug, seed_park_with_tracker)
    row = _report(db_session, seed_mechanic, seed_park_with_tracker, target=target)
    rbac.set_user_effective_permissions(db_session, user, ["reports.create", "nav.reports"])
    db_session.commit()
    login_as(client, user.username, "secret")
    assert client.get(f"/reports/{row.id}").status_code == 403
    assert client.post(f"/reports/{row.id}/done").status_code == 403
    assert client.post(f"/reports/{row.id}/return", json={"comment": "No"}).status_code == 403
    assert client.post(f"/reports/{row.id}/escalate", json={"comment": "No"}).status_code == 403
    assert client.get("/reports/inbox").status_code == 403
    assert client.get("/reports/badge").json() == {"count": 0}
    db_session.refresh(row)
    assert row.status == "open"


def test_driver_can_read_own_report_but_not_others_or_process(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    user = _user(db_session, "driver", seed_park_with_tracker)
    own = _report(db_session, user, seed_park_with_tracker, state="returned")
    other = _report(db_session, seed_mechanic, seed_park_with_tracker)
    login_as(client, user.username, "secret")
    assert client.get(f"/reports/{own.id}").status_code == 200
    assert [row["id"] for row in client.get("/reports/mine").json()] == [own.id]
    assert client.get(f"/reports/{other.id}").status_code == 403
    assert client.post(f"/reports/{other.id}/done").status_code == 403
    assert client.get("/reports/badge").json() == {"count": 1}


def test_dual_author_resolver_badge_combines_own_returned_and_inbox(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    user = _user(db_session, "operator", seed_park_with_tracker)
    _report(db_session, user, seed_park_with_tracker, state="returned")
    _report(db_session, seed_mechanic, seed_park_with_tracker)
    login_as(client, user.username, "secret")
    assert client.get("/reports/badge").json() == {"count": 2}


@pytest.mark.parametrize("loss", ["approval", "assignment", "active_park"])
def test_report_scope_loss_hides_own_and_inbox_data(
    client, db_session, seed_mechanic, seed_park_with_tracker, loss
):
    user = _user(db_session, "operator", seed_park_with_tracker)
    own = _report(db_session, user, seed_park_with_tracker, state="returned")
    other = _report(db_session, seed_mechanic, seed_park_with_tracker)
    if loss == "approval":
        user.access_status = "pending"
    elif loss == "assignment":
        db_session.execute(delete(UserPark).where(UserPark.user_id == user.id))
    else:
        seed_park_with_tracker.is_active = False
    db_session.commit()
    login_as(client, user.username, "secret")
    assert client.get(f"/reports/{own.id}").status_code == 403
    assert client.get(f"/reports/{other.id}").status_code == 403
    assert client.post(f"/reports/{other.id}/done").status_code == 403
    mine = client.get("/reports/mine")
    if loss == "approval":
        assert mine.status_code == 403
    else:
        assert mine.status_code == 200
        assert mine.json() == []


def test_admin_inbox_and_badge_follow_same_park_filter(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    user = _user(db_session, "admin")
    other = Park(name="Other", tag="Other", is_active=True)
    db_session.add(other)
    db_session.commit()
    _report(db_session, seed_mechanic, seed_park_with_tracker, target="admin")
    _report(db_session, seed_mechanic, other, target="admin")
    login_as(client, user.username, "secret")
    assert len(client.get(f"/reports/inbox?park_id={other.id}").json()) == 1
    assert client.get(f"/reports/badge?park_id={other.id}").json() == {"count": 1}


def test_royal_routes_all_reports_without_widening_other_role_boundaries(
    db_session, seed_mechanic, seed_park_with_tracker
):
    alpha = seed_park_with_tracker
    beta = Park(name="Beta", tag="Beta", is_active=True)
    db_session.add(beta)
    db_session.commit()

    royal = _user(db_session, "royal")
    admin = _user(db_session, "admin")
    operator = _user(db_session, "operator", alpha)
    driver = _user(db_session, "driver", alpha)

    operator_report = _report(db_session, seed_mechanic, alpha, target="operator")
    admin_report = _report(db_session, seed_mechanic, beta, target="admin")
    royal_open_report = _report(db_session, royal, beta, target="operator")
    returned_report = _report(db_session, royal, alpha, state="returned")
    done_report = _report(db_session, seed_mechanic, alpha, state="done")
    stale_cookie_report = reports_svc.ensure_open_emergency_cookie_report(db_session, author=royal)
    assert stale_cookie_report is not None

    assert {row.id for row in reports_svc.list_inbox(db_session, royal)} == {
        operator_report.id,
        admin_report.id,
        royal_open_report.id,
        returned_report.id,
        done_report.id,
        stale_cookie_report.id,
    }
    assert reports_svc.badge_counts(db_session, royal) == {"count": 5}

    assert {row.id for row in reports_svc.list_inbox(db_session, admin)} == {
        operator_report.id,
        admin_report.id,
        royal_open_report.id,
        returned_report.id,
        done_report.id,
        stale_cookie_report.id,
    }
    assert reports_svc.badge_counts(db_session, admin) == {"count": 4}

    assert [row.id for row in reports_svc.list_inbox(db_session, operator)] == [operator_report.id]
    assert reports_svc.badge_counts(db_session, operator) == {"count": 1}

    for user in (seed_mechanic, driver):
        with pytest.raises(PermissionError):
            reports_svc.list_inbox(db_session, user)
        assert reports_svc.badge_counts(db_session, user) == {"count": 0}

    assert reports_svc.get_report(db_session, royal, operator_report.id).id == operator_report.id
    assert (
        reports_svc.get_report(db_session, royal, stale_cookie_report.id).id
        == stale_cookie_report.id
    )
    assert (
        reports_svc.get_report(db_session, seed_mechanic, operator_report.id).id
        == operator_report.id
    )
    assert reports_svc.get_report(db_session, admin, operator_report.id).id == operator_report.id
    with pytest.raises(PermissionError):
        reports_svc.get_report(db_session, driver, stale_cookie_report.id)

    admin_action = _report(db_session, seed_mechanic, alpha, target="admin")
    operator_action = _report(db_session, seed_mechanic, alpha, target="operator")
    assert reports_svc.done_report(db_session, royal, admin_action.id).status == "done"
    assert reports_svc.done_report(db_session, operator, operator_action.id).status == "done"
    assert reports_svc.done_report(db_session, royal, operator_report.id).status == "done"
    assert reports_svc.done_report(db_session, admin, royal_open_report.id).status == "done"
    with pytest.raises(PermissionError):
        reports_svc.done_report(db_session, seed_mechanic, operator_report.id)
    with pytest.raises(PermissionError):
        reports_svc.done_report(db_session, driver, operator_report.id)


def test_author_cannot_attach_after_losing_report_park_scope(
    client, db_session, seed_mechanic, seed_park_with_tracker
):
    row = _report(db_session, seed_mechanic, seed_park_with_tracker)
    db_session.execute(delete(UserPark).where(UserPark.user_id == seed_mechanic.id))
    db_session.commit()
    login_as(client, "mech1", "secret")
    response = client.post(
        f"/reports/{row.id}/attachments",
        data={"kind": "client_log"},
        files={"file": ("log.txt", b"log content", "text/plain")},
    )
    assert response.status_code == 403
    assert db_session.scalar(select(ReportAttachment.id)) is None
