from datetime import date, timedelta

import pytest
from sqlalchemy import select

from conftest import login_as, role_id_for
from robopark_api.models import AuditLog, Campaign, CampaignSubmission, Park, Report, User, UserPark
from robopark_api.security import hash_password
from robopark_api.services import campaigns as campaigns_svc


def _user(db, slug, *, name, park=None):
    user = User(
        username=name,
        password_hash=hash_password("secret"),
        role_id=role_id_for(db, slug),
        access_status="approved",
        is_active=True,
    )
    db.add(user)
    db.flush()
    if park is not None:
        db.add(UserPark(user_id=user.id, park_id=park.id))
    db.commit()
    return user


def _campaign_payload(park_id):
    return {
        "kind": "service_company",
        "name": "СК Альфа",
        "tracker_tag": "service-2026",
        "park_ids": [park_id],
        "starts_on": str(date.today()),
        "due_on": str(date.today() + timedelta(days=10)),
    }


def _issue(key="ROBOPARK-42", *, tags=("service-2026", "Alpha")):
    return {
        "key": key,
        "summary": "A042 замена корпуса",
        "status": "Открыт",
        "status_key": "open",
        "queue": "ROBOPARK",
        "robot": "42",
        "created": "2026-09-09T10:00:00+00:00",
        "updated": "2026-09-09T11:00:00+00:00",
        "hours_created": "3",
        "tags": list(tags),
        "priority": "normal",
        "type": "service",
    }


def test_admin_creates_campaign_and_every_role_reads_park_metrics(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-royal")
    mechanic = _user(db_session, "mechanic", name="campaign-mechanic", park=seed_park_with_tracker)
    monkeypatch.setattr(campaigns_svc.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(campaigns_svc.tracker_cache, "search_issues", lambda **kwargs: [_issue()])

    login_as(client, royal.username, "secret")
    created = client.post("/campaigns", json=_campaign_payload(seed_park_with_tracker.id))
    assert created.status_code == 201
    campaign_id = created.json()["id"]

    login_as(client, mechanic.username, "secret")
    listing = client.get("/campaigns").json()
    assert listing[0]["id"] == campaign_id
    assert listing[0]["total_count"] == 1
    assert listing[0]["completed_count"] == 0
    assert listing[0]["percent_complete"] == 0

    detail = client.get(f"/campaigns/{campaign_id}").json()
    assert [ticket["key"] for ticket in detail["open_tickets"]] == ["ROBOPARK-42"]
    assert detail["closed_tickets"] == []


def test_park_filtered_campaign_metrics_do_not_include_another_park(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    second = Park(name="South", tag="South", tracker_queue="ROBOPARK", is_active=True)
    db_session.add(second)
    db_session.commit()
    royal = _user(db_session, "royal", name="campaign-scope")
    monkeypatch.setattr(campaigns_svc.settings_svc, "get_tracker_token", lambda db: "token")

    def issues(**kwargs):
        query = kwargs["query"]
        return (
            [_issue("ROBOPARK-42")]
            if "Alpha" in query
            else [_issue("ROBOPARK-43", tags=("service-2026", "South"))]
        )

    monkeypatch.setattr(campaigns_svc.tracker_cache, "search_issues", issues)
    login_as(client, royal.username, "secret")
    payload = _campaign_payload(seed_park_with_tracker.id)
    payload["park_ids"] = [seed_park_with_tracker.id, second.id]
    client.post("/campaigns", json=payload)

    scoped = client.get(f"/campaigns?park_id={seed_park_with_tracker.id}").json()[0]
    assert scoped["park_ids"] == [seed_park_with_tracker.id]
    assert scoped["total_count"] == 1


@pytest.mark.parametrize(
    ("transitions", "expected_transition", "expected_id"),
    [
        (
            [
                {"id": "diagnostics", "display": "Диагностика"},
                {"id": "review", "display": "Проверка оператором"},
            ],
            "review",
            "review",
        ),
        ([{"id": "diagnostics", "display": "Диагностика"}], "diagnostics", "diagnostics"),
    ],
)
def test_completion_requires_comment_and_photo_then_creates_operator_review(
    client,
    db_session,
    seed_park_with_tracker,
    monkeypatch,
    transitions,
    expected_transition,
    expected_id,
):
    royal = _user(db_session, "royal", name="campaign-admin")
    mechanic = _user(db_session, "mechanic", name="campaign-worker", park=seed_park_with_tracker)
    monkeypatch.setattr(campaigns_svc.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(campaigns_svc.tracker_cache, "search_issues", lambda **kwargs: [_issue()])
    monkeypatch.setattr(campaigns_svc.tracker_cache, "get_issue", lambda **kwargs: _issue())
    monkeypatch.setattr(
        campaigns_svc.tracker_cache,
        "list_transitions",
        lambda **kwargs: transitions,
    )
    transitioned = []
    monkeypatch.setattr(
        campaigns_svc.tracker_client,
        "transition_issue",
        lambda **kwargs: transitioned.append(kwargs),
    )

    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]

    login_as(client, mechanic.username, "secret")
    missing = client.post(
        f"/campaigns/{campaign_id}/tickets/ROBOPARK-42/complete",
        data={"comment": "Готово", "park_id": str(seed_park_with_tracker.id)},
    )
    assert missing.status_code == 422

    completed = client.post(
        f"/campaigns/{campaign_id}/tickets/ROBOPARK-42/complete",
        data={"comment": "Оклейка завершена", "park_id": str(seed_park_with_tracker.id)},
        files={"photo": ("done.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
    )
    assert completed.status_code == 201, completed.text
    assert completed.json()["review_status"] == "open"
    assert completed.json()["tracker_transition"] == expected_transition
    assert transitioned == [{"token": "token", "key": "ROBOPARK-42", "transition": expected_id}]

    submission = db_session.scalar(select(CampaignSubmission))
    report = db_session.get(Report, submission.report_id)
    assert report.kind == "campaign_review"
    assert report.target_role == "operator"
    assert report.park_id == seed_park_with_tracker.id
    assert report.tracker_key == "ROBOPARK-42"
    assert f"Tracker: {expected_transition}" in report.body
    assert report.attachments[0].kind == "device_photo"
    audit = db_session.scalar(select(AuditLog).where(AuditLog.target_id == "ROBOPARK-42"))
    assert audit.action == "tracker.transition"
    assert audit.outcome == "success"

    detail = client.get(f"/campaigns/{campaign_id}").json()
    assert detail["open_tickets"] == []
    assert detail["closed_tickets"][0]["review_status"] == "open"
    assert detail["closed_tickets"][0]["tracker_transition"] == expected_transition
    assert detail["completed_count"] == 1
    assert detail["percent_complete"] == 100


def test_non_admin_cannot_create_or_complete_foreign_campaign(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-owner")
    driver = _user(db_session, "driver", name="campaign-driver")
    monkeypatch.setattr(campaigns_svc.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(campaigns_svc.tracker_cache, "search_issues", lambda **kwargs: [_issue()])

    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]

    login_as(client, driver.username, "secret")
    assert (
        client.post("/campaigns", json=_campaign_payload(seed_park_with_tracker.id)).status_code
        == 403
    )
    assert client.get(f"/campaigns/{campaign_id}").status_code == 403
    assert db_session.scalar(select(Campaign).where(Campaign.id == campaign_id)) is not None
