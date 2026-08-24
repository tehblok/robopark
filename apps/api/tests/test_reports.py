import pytest

from robopark_api.models import Report, UserRole
from robopark_api.services import reports as reports_svc


def test_create_manual_ticket_question_success(db_session, seed_mechanic, seed_park_with_tracker):
    report = reports_svc.create_manual_report(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        kind=reports_svc.KIND_TICKET_QUESTION,
        title="Question about ROBO-1",
        body="What is the status?",
        tracker_key="ROBO-1",
        tracker_url="https://tracker.yandex.ru/ROBO-1",
    )

    assert report.id is not None
    assert report.kind == reports_svc.KIND_TICKET_QUESTION
    assert report.status == reports_svc.STATUS_OPEN
    assert report.park_id == seed_park_with_tracker.id
    assert report.author_user_id == seed_mechanic.id
    assert report.target_role == UserRole.operator.value
    assert report.tracker_key == "ROBO-1"
    assert report.tracker_url == "https://tracker.yandex.ru/ROBO-1"
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
    with pytest.raises(ValueError, match="invalid manual report kind"):
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
        tracker_url="https://tracker.yandex.ru/ROBO-99",
        title="Закрытие ROBO-99",
    )

    assert report.id is not None
    assert report.kind == reports_svc.KIND_TICKET_CLOSE_REVIEW
    assert report.status == reports_svc.STATUS_OPEN
    assert report.park_id == seed_park_with_tracker.id
    assert report.author_user_id == seed_mechanic.id
    assert report.target_role == UserRole.operator.value
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
        tracker_url="https://tracker.yandex.ru/ROBO-42",
        title="Закрытие ROBO-42",
    )
    second = reports_svc.get_or_create_close_review(
        db_session,
        author=seed_mechanic,
        park_id=seed_park_with_tracker.id,
        tracker_key="ROBO-42",
        tracker_url="https://tracker.yandex.ru/ROBO-42",
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
