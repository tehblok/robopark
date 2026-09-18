from datetime import date

import pytest
from fastapi.testclient import TestClient

from conftest import login_as, role_id_for
from robopark_api.models import (
    AccessStatus,
    AuditLog,
    Campaign,
    CampaignSubmission,
    Park,
    Report,
    ReportAttachment,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import report_attachments as attachments_svc
from robopark_api.services import reports as reports_svc
from robopark_api.services.rbac import RoleSlug


@pytest.fixture
def seed_operator_with_park(db_session, seed_park_with_tracker):
    user = User(
        username="operator1",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=seed_park_with_tracker.id))
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def seed_admin(db_session):
    user = User(
        username="admin1",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "admin"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def seed_other_park(db_session):
    park = Park(
        name="Beta",
        tag="Beta",
        is_active=True,
        tracker_queue="ROBOPARK",
        feature_blockers=True,
    )
    db_session.add(park)
    db_session.commit()
    db_session.refresh(park)
    return park


@pytest.fixture
def seed_operator_other_park(db_session, seed_other_park):
    user = User(
        username="operator2",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "operator"),
        access_status=AccessStatus.approved.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(UserPark(user_id=user.id, park_id=seed_other_park.id))
    db_session.commit()
    db_session.refresh(user)
    return user


def _create_open_report(db_session, *, author, park_id, title="Test report"):
    # Seed historical reports even when the author no longer has park access.
    report = Report(
        author_user_id=author.id,
        park_id=park_id,
        kind=reports_svc.KIND_TICKET_QUESTION,
        status=reports_svc.STATUS_OPEN,
        target_role=RoleSlug.OPERATOR,
        title=title,
        body="Body",
        tracker_key="ROBO-1",
        tracker_url="https://st.yandex-team.ru/ROBO-1",
    )
    db_session.add(report)
    db_session.commit()
    return report


def test_create_manual_ticket_question_success(db_session, seed_mechanic, seed_park_with_tracker):
    report = reports_svc.create_manual_report(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        kind=reports_svc.KIND_TICKET_QUESTION,
        title="Question about ROBO-1",
        body="What is the status?",
        tracker_key="ROBO-1",
        tracker_url="https://st.yandex-team.ru/ROBO-1",
    )

    assert report.id is not None
    assert report.kind == reports_svc.KIND_TICKET_QUESTION
    assert report.status == reports_svc.STATUS_OPEN
    assert report.park_id == seed_park_with_tracker.id
    assert report.author_user_id == seed_mechanic.id
    assert report.target_role == RoleSlug.OPERATOR
    assert report.tracker_key == "ROBO-1"
    assert report.tracker_url == "https://st.yandex-team.ru/ROBO-1"
    assert report.title == "Question about ROBO-1"
    assert report.body == "What is the status?"


def test_create_manual_mechanic_problem_without_tracker_key(
    db_session, seed_mechanic, seed_park_with_tracker
):
    report = reports_svc.create_manual_report(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        kind=reports_svc.KIND_MECHANIC_PROBLEM,
        title="Hydraulic leak",
        body="Robot 42 has a leak",
        tracker_key=None,
        tracker_url=None,
    )

    assert report.kind == reports_svc.KIND_MECHANIC_PROBLEM
    assert report.tracker_key is None
    assert report.tracker_url is None


def test_create_manual_ticket_question_requires_tracker_key(
    db_session, seed_mechanic, seed_park_with_tracker
):
    with pytest.raises(ValueError, match="tracker_key"):
        reports_svc.create_manual_report(
            db_session,
            author=seed_mechanic,
            park_id=seed_park_with_tracker.id,
            kind=reports_svc.KIND_TICKET_QUESTION,
            title="Missing key",
            body="No ticket linked",
            tracker_key=None,
            tracker_url=None,
        )


@pytest.mark.parametrize(
    "kind",
    [
        reports_svc.KIND_TICKET_CLOSE_REVIEW,
        reports_svc.KIND_ESCALATION,
        "unknown_kind",
    ],
)
def test_create_manual_rejects_non_manual_kind(
    db_session, seed_mechanic, seed_park_with_tracker, kind
):
    with pytest.raises(ValueError, match="invalid_report_kind"):
        reports_svc.create_manual_report(
            db_session,
            author=seed_mechanic,
            park_id=seed_park_with_tracker.id,
            kind=kind,
            title="Bad kind",
            body="Should fail",
            tracker_key="ROBO-1",
            tracker_url=None,
        )


def test_get_or_create_close_review_inserts(db_session, seed_mechanic, seed_park_with_tracker):
    report = reports_svc.get_or_create_close_review(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        tracker_key="ROBO-99",
        tracker_url="https://st.yandex-team.ru/ROBO-99",
        title="Закрытие ROBO-99",
    )

    assert report.id is not None
    assert report.kind == reports_svc.KIND_TICKET_CLOSE_REVIEW
    assert report.status == reports_svc.STATUS_OPEN
    assert report.park_id == seed_park_with_tracker.id
    assert report.author_user_id == seed_mechanic.id
    assert report.target_role == RoleSlug.OPERATOR
    assert report.tracker_key == "ROBO-99"
    assert report.title == "Закрытие ROBO-99"
    assert report.body == ""


def test_get_or_create_close_review_returns_existing_open(
    db_session, seed_mechanic, seed_park_with_tracker
):
    first = reports_svc.get_or_create_close_review(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        tracker_key="ROBO-42",
        tracker_url="https://st.yandex-team.ru/ROBO-42",
        title="Закрытие ROBO-42",
    )
    second = reports_svc.get_or_create_close_review(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        tracker_key="ROBO-42",
        tracker_url="https://st.yandex-team.ru/ROBO-42",
        title="Duplicate attempt",
    )

    assert second.id == first.id
    assert db_session.query(Report).count() == 1


def test_get_or_create_close_review_allows_new_after_done(
    db_session, seed_mechanic, seed_park_with_tracker
):
    first = reports_svc.get_or_create_close_review(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        tracker_key="ROBO-7",
        tracker_url=None,
        title="First close review",
    )
    first.status = reports_svc.STATUS_DONE
    db_session.commit()

    second = reports_svc.get_or_create_close_review(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        tracker_key="ROBO-7",
        tracker_url=None,
        title="Second close review",
    )

    assert second.id != first.id
    assert second.status == reports_svc.STATUS_OPEN
    assert db_session.query(Report).count() == 2


def test_list_inbox_operator_sees_open_reports_in_assigned_park(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    inbox = reports_svc.list_inbox(db_session, seed_operator_with_park)

    assert [r.id for r in inbox] == [report.id]


def test_list_inbox_operator_excludes_other_parks(
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_operator_other_park,
    seed_park_with_tracker,
    seed_other_park,
):
    _create_open_report(db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id)
    other_report = _create_open_report(
        db_session,
        author=seed_mechanic,
        park_id=seed_other_park.id,
        title="Other park",
    )

    inbox = reports_svc.list_inbox(db_session, seed_operator_other_park)

    assert [r.id for r in inbox] == [other_report.id]


def test_list_inbox_operator_filters_by_park_id(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    inbox = reports_svc.list_inbox(
        db_session, seed_operator_with_park, park_id=seed_park_with_tracker.id
    )

    assert [r.id for r in inbox] == [report.id]


def test_list_inbox_operator_park_filter_cross_park_forbidden(
    db_session, seed_mechanic, seed_operator_with_park, seed_other_park
):
    _create_open_report(db_session, author=seed_mechanic, park_id=seed_other_park.id, title="Other")

    with pytest.raises(PermissionError):
        reports_svc.list_inbox(db_session, seed_operator_with_park, park_id=seed_other_park.id)


def test_list_inbox_admin_sees_open_escalations(
    db_session, seed_mechanic, seed_operator_with_park, seed_admin, seed_park_with_tracker
):
    parent = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    escalation = reports_svc.escalate_report(
        db_session, seed_operator_with_park, parent.id, "Need admin help"
    )

    inbox = reports_svc.list_inbox(db_session, seed_admin)

    assert [r.id for r in inbox] == [escalation.id, parent.id]
    assert escalation.kind == reports_svc.KIND_ESCALATION
    assert escalation.target_role == RoleSlug.ADMIN


def test_list_inbox_excludes_non_open(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    reports_svc.done_report(db_session, seed_operator_with_park, report.id)

    inbox = reports_svc.list_inbox(db_session, seed_operator_with_park)

    assert inbox == []


def test_royal_inbox_contains_open_returned_and_done_reports(
    db_session, seed_mechanic, seed_operator_with_park, seed_royal, seed_park_with_tracker
):
    opened = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="Open"
    )
    returned = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="Returned"
    )
    done = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="Done"
    )
    reports_svc.return_report(db_session, seed_operator_with_park, returned.id, "Fix")
    reports_svc.done_report(db_session, seed_operator_with_park, done.id)

    inbox = reports_svc.list_inbox(db_session, seed_royal)

    assert {item.id for item in inbox} == {opened.id, returned.id, done.id}


def test_list_mine_returns_author_reports(db_session, seed_mechanic, seed_park_with_tracker):
    first = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="One"
    )
    second = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="Two"
    )

    mine = reports_svc.list_mine(db_session, seed_mechanic)

    assert [r.id for r in mine] == [second.id, first.id]


def test_get_report_author_can_view(db_session, seed_mechanic, seed_park_with_tracker):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    fetched = reports_svc.get_report(db_session, seed_mechanic, report.id)

    assert fetched.id == report.id


def test_get_report_operator_in_park_can_view(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    fetched = reports_svc.get_report(db_session, seed_operator_with_park, report.id)

    assert fetched.id == report.id


def test_get_report_cross_park_forbidden(
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_other_park,
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_other_park.id, title="Other"
    )

    with pytest.raises(PermissionError):
        reports_svc.get_report(db_session, seed_operator_with_park, report.id)


def test_get_report_not_found(db_session, seed_mechanic):
    with pytest.raises(LookupError):
        reports_svc.get_report(db_session, seed_mechanic, 99999)


def test_return_report_success(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    updated = reports_svc.return_report(
        db_session, seed_operator_with_park, report.id, "Please fix tracker link"
    )

    assert updated.status == reports_svc.STATUS_RETURNED
    assert updated.return_comment == "Please fix tracker link"


def test_return_report_requires_comment(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    with pytest.raises(ValueError, match="comment"):
        reports_svc.return_report(db_session, seed_operator_with_park, report.id, "  ")


def test_return_report_mechanic_forbidden(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    with pytest.raises(PermissionError):
        reports_svc.return_report(db_session, seed_mechanic, report.id, "Nope")


def test_done_report_success(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    updated = reports_svc.done_report(db_session, seed_operator_with_park, report.id)

    assert updated.status == reports_svc.STATUS_DONE
    assert updated.resolved_at is not None


def test_author_can_edit_and_resubmit_returned_report(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    reports_svc.return_report(db_session, seed_operator_with_park, report.id, "Add details")

    updated = reports_svc.resubmit_report(
        db_session,
        seed_mechanic,
        report.id,
        title="Updated title",
        body="Updated body",
        tracker_key="ROBO-2",
        tracker_url="https://st.yandex-team.ru/ROBO-2",
    )

    assert updated.status == reports_svc.STATUS_OPEN
    assert updated.title == "Updated title"
    assert updated.body == "Updated body"
    assert updated.tracker_key == "ROBO-2"
    assert updated.return_comment is None


def test_escalate_report_creates_child_parent_stays_open(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    parent = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    child = reports_svc.escalate_report(
        db_session, seed_operator_with_park, parent.id, "Escalating to admin"
    )

    db_session.refresh(parent)
    assert parent.status == reports_svc.STATUS_OPEN
    assert child.id != parent.id
    assert child.kind == reports_svc.KIND_ESCALATION
    assert child.status == reports_svc.STATUS_OPEN
    assert child.target_role == RoleSlug.ADMIN
    assert child.parent_report_id == parent.id
    assert child.author_user_id == seed_operator_with_park.id
    assert child.body == "Escalating to admin"
    assert child.park_id == parent.park_id
    assert child.tracker_key == parent.tracker_key


def test_escalate_report_admin_forbidden(
    db_session, seed_mechanic, seed_admin, seed_operator_with_park, seed_park_with_tracker
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )

    with pytest.raises(PermissionError):
        reports_svc.escalate_report(db_session, seed_admin, report.id, "Admin cannot")


def test_badge_counts_mechanic_returned(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    open_report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="Open"
    )
    returned_report = _create_open_report(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        title="Returned",
    )
    reports_svc.return_report(db_session, seed_operator_with_park, returned_report.id, "Fix it")
    reports_svc.done_report(db_session, seed_operator_with_park, open_report.id)

    assert reports_svc.badge_counts(db_session, seed_mechanic) == {"count": 1}


def test_badge_counts_operator_open_inbox(
    db_session, seed_mechanic, seed_operator_with_park, seed_park_with_tracker
):
    _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="One"
    )
    second = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="Two"
    )
    reports_svc.done_report(db_session, seed_operator_with_park, second.id)

    assert reports_svc.badge_counts(db_session, seed_operator_with_park) == {"count": 1}


def test_badge_counts_operator_filters_by_park_id(
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_other_park,
    seed_park_with_tracker,
):
    _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="Alpha"
    )
    _create_open_report(db_session, author=seed_mechanic, park_id=seed_other_park.id, title="Beta")

    assert reports_svc.badge_counts(
        db_session, seed_operator_with_park, park_id=seed_park_with_tracker.id
    ) == {"count": 1}
    assert reports_svc.badge_counts(db_session, seed_operator_with_park) == {"count": 1}


def test_badge_counts_operator_cross_park_filter_forbidden(
    db_session, seed_operator_with_park, seed_other_park
):
    with pytest.raises(PermissionError):
        reports_svc.badge_counts(db_session, seed_operator_with_park, park_id=seed_other_park.id)


def test_badge_counts_admin_open_escalations(
    db_session, seed_mechanic, seed_operator_with_park, seed_admin, seed_park_with_tracker
):
    parent = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    reports_svc.escalate_report(db_session, seed_operator_with_park, parent.id, "Need help")

    assert reports_svc.badge_counts(db_session, seed_admin) == {"count": 2}


# --- HTTP router tests ---


def _login(client: TestClient, username: str) -> None:
    login_as(client, username, "secret")


def test_manager_cannot_delete_campaign_result_until_submission_is_removed(
    client, db_session, seed_admin, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    campaign = Campaign(
        kind="service_company",
        name="Test",
        tracker_tag="SC",
        starts_on=date(2026, 9, 1),
        due_on=date(2026, 9, 30),
        created_by=seed_admin.id,
    )
    db_session.add(campaign)
    db_session.flush()
    submission = CampaignSubmission(
        campaign_id=campaign.id,
        issue_key="ROBO-1",
        park_id=seed_park_with_tracker.id,
        comment="Работа выполнена",
        author_user_id=seed_mechanic.id,
        report_id=report.id,
    )
    child = Report(
        author_user_id=seed_mechanic.id,
        park_id=seed_park_with_tracker.id,
        kind=reports_svc.KIND_ESCALATION,
        status="open",
        target_role=RoleSlug.ADMIN,
        title="Связанный репорт",
        body="",
        parent_report_id=report.id,
    )
    db_session.add_all([submission, child])
    db_session.flush()
    attachment = ReportAttachment(
        report_id=report.id,
        kind="client_log",
        filename="log.txt",
        content_type="text/plain",
        size_bytes=4,
        storage_key=f"{report.id}/test-file",
    )
    db_session.add(attachment)
    db_session.commit()
    monkeypatch.setattr(attachments_svc, "attachments_root", lambda: tmp_path)
    file_path = tmp_path / str(report.id) / "test-file"
    file_path.parent.mkdir()
    file_path.write_bytes(b"test")
    report_id, child_id, submission_id = report.id, child.id, submission.id

    _login(client, "mech1")
    assert client.delete(f"/reports/{report_id}").status_code == 403
    _login(client, "admin1")
    conflict = client.delete(f"/reports/{report_id}")
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "report_linked_to_campaign"
    db_session.expire_all()
    assert db_session.get(CampaignSubmission, submission_id).report_id == report_id
    assert file_path.read_bytes() == b"test"

    db_session.delete(db_session.get(CampaignSubmission, submission_id))
    db_session.commit()
    assert client.delete(f"/reports/{report_id}").status_code == 204
    assert client.get(f"/reports/{report_id}").status_code == 404
    db_session.expire_all()
    assert db_session.get(Report, report_id) is None
    assert db_session.get(Report, child_id).parent_report_id is None
    assert db_session.get(CampaignSubmission, submission_id) is None
    assert not file_path.exists()


def test_report_delete_recovers_file_cleanup_after_interruption(
    client, db_session, seed_admin, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    db_session.add(
        ReportAttachment(
            report_id=report.id,
            kind="client_log",
            filename="log.txt",
            content_type="text/plain",
            size_bytes=4,
            storage_key=f"{report.id}/test-file",
        )
    )
    db_session.commit()
    monkeypatch.setattr(attachments_svc, "attachments_root", lambda: tmp_path)
    file_path = tmp_path / str(report.id) / "test-file"
    file_path.parent.mkdir()
    file_path.write_bytes(b"test")
    finish = attachments_svc.reconcile_pending_report_deletions
    monkeypatch.setattr(attachments_svc, "reconcile_pending_report_deletions", lambda _db: 0)
    _login(client, "admin1")
    response = client.delete(f"/reports/{report.id}")
    assert response.status_code == 204
    db_session.expire_all()
    assert db_session.get(Report, report.id) is None
    assert file_path.read_bytes() == b"test"
    assert (tmp_path / ".delete-pending" / f"{report.id}.json").exists()
    assert (
        db_session.query(AuditLog)
        .filter_by(action="reports.delete", target_id=str(report.id))
        .count()
        == 1
    )
    assert finish(db_session) == 1
    assert not file_path.exists()
    assert not (tmp_path / ".delete-pending" / f"{report.id}.json").exists()


def test_crash_before_report_delete_commit_keeps_report_attachment(
    db_session, seed_mechanic, seed_park_with_tracker, tmp_path, monkeypatch
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    monkeypatch.setattr(attachments_svc, "attachments_root", lambda: tmp_path)
    key = f"{report.id}/test-file"
    path = tmp_path / key
    path.parent.mkdir()
    path.write_bytes(b"test")
    db_session.add(
        ReportAttachment(
            report_id=report.id,
            kind="client_log",
            filename="log.txt",
            content_type="text/plain",
            size_bytes=4,
            storage_key=key,
        )
    )
    db_session.commit()
    attachments_svc.mark_report_files_for_deletion(report.id, [key])
    assert attachments_svc.reconcile_pending_report_deletions(db_session) == 0
    assert db_session.get(Report, report.id) is not None
    assert path.read_bytes() == b"test"
    assert not (tmp_path / ".delete-pending" / f"{report.id}.json").exists()


def test_http_mechanic_create_report(client: TestClient, seed_mechanic, seed_park_with_tracker):
    _login(client, "mech1")
    r = client.post(
        "/reports",
        json={
            "kind": "ticket_question",
            "park_id": seed_park_with_tracker.id,
            "title": "Question about ROBO-1",
            "body": "What is the status?",
            "tracker_key": "ROBO-1",
            "tracker_url": "https://st.yandex-team.ru/ROBO-1",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "ticket_question"
    assert body["status"] == "open"
    assert body["author_user_id"] == seed_mechanic.id
    assert body["park_id"] == seed_park_with_tracker.id


def test_http_mechanic_create_wrong_park_forbidden(
    client: TestClient, seed_mechanic, seed_other_park
):
    _login(client, "mech1")
    r = client.post(
        "/reports",
        json={
            "kind": "mechanic_problem",
            "park_id": seed_other_park.id,
            "title": "Wrong park",
            "body": "Should fail",
        },
    )
    assert r.status_code == 403


def test_http_operator_inbox(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_park_with_tracker,
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    _login(client, "operator1")
    r = client.get("/reports/inbox")
    assert r.status_code == 200
    assert [item["id"] for item in r.json()] == [report.id]


def test_http_operator_inbox_cross_park_filter_forbidden(
    client: TestClient, seed_operator_with_park, seed_other_park
):
    _login(client, "operator1")
    r = client.get(f"/reports/inbox?park_id={seed_other_park.id}")
    assert r.status_code == 403


def test_http_return_report(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_park_with_tracker,
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    _login(client, "operator1")
    r = client.post(
        f"/reports/{report.id}/return",
        json={"comment": "Please fix tracker link"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "returned"
    assert r.json()["return_comment"] == "Please fix tracker link"


def test_http_return_requires_comment(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_park_with_tracker,
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    _login(client, "operator1")
    r = client.post(f"/reports/{report.id}/return", json={"comment": "  "})
    assert r.status_code == 400


def test_http_done_report(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_park_with_tracker,
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    _login(client, "operator1")
    r = client.post(f"/reports/{report.id}/done")
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["resolved_at"] is not None


def test_http_escalate_report(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_park_with_tracker,
):
    parent = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id
    )
    _login(client, "operator1")
    r = client.post(
        f"/reports/{parent.id}/escalate",
        json={"comment": "Escalating to admin"},
    )
    assert r.status_code == 200
    child = r.json()
    assert child["kind"] == "escalation_to_admin"
    assert child["status"] == "open"
    assert child["parent_report_id"] == parent.id


def test_http_badge_operator(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_park_with_tracker,
):
    _create_open_report(db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id)
    _login(client, "operator1")
    r = client.get(f"/reports/badge?park_id={seed_park_with_tracker.id}")
    assert r.status_code == 200
    assert r.json() == {"count": 1}


def test_http_badge_operator_cross_park_forbidden(
    client: TestClient, seed_operator_with_park, seed_other_park
):
    _login(client, "operator1")
    r = client.get(f"/reports/badge?park_id={seed_other_park.id}")
    assert r.status_code == 403


def test_http_get_report_cross_park_forbidden(
    client: TestClient,
    db_session,
    seed_mechanic,
    seed_operator_with_park,
    seed_other_park,
):
    report = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_other_park.id, title="Other"
    )
    _login(client, "operator1")
    r = client.get(f"/reports/{report.id}")
    assert r.status_code == 403


def test_http_list_mine(client: TestClient, db_session, seed_mechanic, seed_park_with_tracker):
    first = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="One"
    )
    second = _create_open_report(
        db_session, author=seed_mechanic, park_id=seed_park_with_tracker.id, title="Two"
    )
    _login(client, "mech1")
    r = client.get("/reports/mine")
    assert r.status_code == 200
    assert [item["id"] for item in r.json()] == [second.id, first.id]
