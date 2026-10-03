import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from conftest import login_as, role_id_for
from robopark_api.models import (
    AuditLog,
    Campaign,
    CampaignSnapshotTicket,
    CampaignSubmission,
    Park,
    Report,
    ReportAttachment,
    User,
    UserPark,
)
from robopark_api.security import hash_password
from robopark_api.services import campaigns as campaigns_svc
from robopark_api.services import tracker_outbox
from robopark_api.task_workflow_models import ReliableAction


def test_refresh_loop_drains_due_campaigns_without_idle_delay(monkeypatch):
    calls = []

    async def run():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def process(_factory):
            calls.append(1)
            if len(calls) == 3:
                loop.call_soon_threadsafe(stop.set)
            return 1

        monkeypatch.setattr(campaigns_svc, "process_refresh_batch", process)
        await asyncio.wait_for(campaigns_svc.run_refresh_loop(object(), stop), timeout=4)

    asyncio.run(run())
    assert len(calls) == 3


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
        "tracker_tag": "замена корпуса",
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


def _seed_snapshot(db, campaign_id, park_id):
    db.add(
        CampaignSnapshotTicket(
            campaign_id=campaign_id,
            issue_key="ROBOPARK-42",
            park_id=park_id,
            summary="A042 замена корпуса",
            status="Открыт",
            status_key="open",
            robot="42",
            rule_revision=1,
        )
    )
    db.commit()


def test_campaign_reads_saved_snapshot_without_tracker(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-offline-royal")
    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    db_session.add(
        CampaignSnapshotTicket(
            campaign_id=campaign_id,
            issue_key="ROBOPARK-42",
            park_id=seed_park_with_tracker.id,
            summary="A042 замена корпуса",
            status="Открыт",
            status_key="open",
            robot="42",
            rule_revision=1,
        )
    )
    db_session.commit()

    def offline(**_kwargs):
        raise AssertionError("read-only campaign request must not call Tracker")

    monkeypatch.setattr(campaigns_svc.tracker_cache, "search_issues", offline)
    listing = client.get("/campaigns")
    detail = client.get(f"/campaigns/{campaign_id}")
    assert listing.status_code == 200
    assert listing.json()[0]["total_count"] == 1
    assert detail.status_code == 200
    assert [item["key"] for item in detail.json()["open_tickets"]] == ["ROBOPARK-42"]


def test_campaign_list_selects_distinct_order_columns_for_postgres(monkeypatch):
    monkeypatch.setattr(campaigns_svc, "_accessible_park_ids", lambda _db, _user: {1})
    captured = []

    class EmptyDb:
        def scalars(self, statement):
            captured.append(statement)
            return self

        def all(self):
            return []

    assert campaigns_svc.list_campaigns(EmptyDb(), object(), 1) == []
    selected = {column.key for column in captured[0].selected_columns}
    assert {"id", "is_active", "due_on"} <= selected


def test_new_campaign_refresh_matches_normalized_title_and_park(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-summary-royal")
    login_as(client, royal.username, "secret")
    payload = _campaign_payload(seed_park_with_tracker.id)
    payload["tracker_tag"] = "  Замена   КОРПУСА  "
    campaign_id = client.post("/campaigns", json=payload).json()["id"]
    assert db_session.get(Campaign, campaign_id).selection_mode == "summary"
    monkeypatch.setattr(campaigns_svc.settings_svc, "get_tracker_token", lambda db: "token")
    calls = []

    def search(**kwargs):
        calls.append(kwargs["query"])
        return [
            _issue("ROBOPARK-42", tags=("Alpha",)),
            {**_issue("ROBOPARK-43", tags=("Alpha",)), "summary": "Другая работа"},
            {**_issue("ROBOPARK-44", tags=("South",)), "summary": "Замена корпуса"},
        ]

    monkeypatch.setattr(campaigns_svc.tracker_cache, "search_issues", search)
    assert campaigns_svc.process_refresh_batch(sessionmaker(bind=db_session.bind, future=True)) == 1
    assert len(calls) == 1
    rows = db_session.scalars(
        select(CampaignSnapshotTicket).where(CampaignSnapshotTicket.campaign_id == campaign_id)
    ).all()
    assert [row.issue_key for row in rows] == ["ROBOPARK-42"]


def test_campaign_rejects_too_short_summary_rule(client, db_session, seed_park_with_tracker):
    royal = _user(db_session, "royal", name="campaign-short-rule-royal")
    login_as(client, royal.username, "secret")
    payload = _campaign_payload(seed_park_with_tracker.id)
    payload["tracker_tag"] = "  а  "
    response = client.post("/campaigns", json=payload)
    assert response.status_code in {400, 422}


def test_changed_rule_rejects_old_snapshot_candidate_before_refresh(
    client, db_session, seed_park_with_tracker
):
    royal = _user(db_session, "royal", name="campaign-revision-royal")
    mechanic = _user(
        db_session, "mechanic", name="campaign-revision-mechanic", park=seed_park_with_tracker
    )
    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    _seed_snapshot(db_session, campaign_id, seed_park_with_tracker.id)
    assert (
        client.patch(f"/campaigns/{campaign_id}", json={"tracker_tag": "новая работа"}).status_code
        == 200
    )
    login_as(client, mechanic.username, "secret")
    assert client.get(f"/campaigns/{campaign_id}").json()["total_count"] == 0
    response = client.post(
        f"/campaigns/{campaign_id}/tickets/ROBOPARK-42/complete",
        data={"comment": "Готово", "park_id": str(seed_park_with_tracker.id)},
        files={"photo": ("done.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
    )
    assert response.status_code == 404


def test_refresh_requests_coalesce_and_preserve_stale_data_on_outage(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-refresh-royal")
    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    monkeypatch.setattr(campaigns_svc.settings_svc, "get_tracker_token", lambda db: "token")
    calls = []

    def search(**_kwargs):
        calls.append(1)
        return [_issue()]

    monkeypatch.setattr(campaigns_svc.tracker_cache, "search_issues", search)
    factory = sessionmaker(bind=db_session.bind, future=True)
    assert campaigns_svc.process_refresh_batch(factory) == 1
    assert client.get(f"/campaigns/{campaign_id}").json()["total_count"] == 1
    for _ in range(20):
        response = client.post(f"/campaigns/{campaign_id}/refresh")
        assert response.status_code == 202
    assert campaigns_svc.process_refresh_batch(factory) == 1
    assert campaigns_svc.process_refresh_batch(factory) == 0
    assert len(calls) == 2

    def offline(**_kwargs):
        raise campaigns_svc.tracker_client.TrackerError("offline")

    monkeypatch.setattr(campaigns_svc.tracker_cache, "search_issues", offline)
    assert client.post(f"/campaigns/{campaign_id}/refresh").status_code == 202
    assert campaigns_svc.process_refresh_batch(factory) == 1
    detail = client.get(f"/campaigns/{campaign_id}")
    assert detail.status_code == 200
    assert detail.json()["total_count"] == 1
    assert detail.json()["snapshot_state"] == "error"


def test_completion_replays_same_key_offline_without_duplicate_local_objects(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-idempotent-royal")
    mechanic = _user(
        db_session, "mechanic", name="campaign-idempotent-mechanic", park=seed_park_with_tracker
    )
    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    db_session.add(
        CampaignSnapshotTicket(
            campaign_id=campaign_id,
            issue_key="ROBOPARK-42",
            park_id=seed_park_with_tracker.id,
            summary="A042 замена корпуса",
            status="Открыт",
            status_key="open",
            robot="42",
            rule_revision=1,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        campaigns_svc.tracker_cache,
        "get_issue",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("offline completion must not fetch Tracker")
        ),
    )
    login_as(client, mechanic.username, "secret")

    def send(comment="Готово", key="same-click-001"):
        return client.post(
            f"/campaigns/{campaign_id}/tickets/ROBOPARK-42/complete",
            headers={"Idempotency-Key": key},
            data={"comment": comment, "park_id": str(seed_park_with_tracker.id)},
            files={"photo": ("done.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
        )

    first = send()
    second = send()
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]
    assert send(comment="Изменено").status_code == 409
    assert len(db_session.scalars(select(Report)).all()) == 1
    assert len(db_session.scalars(select(CampaignSubmission)).all()) == 1
    assert len(db_session.scalars(select(ReliableAction)).all()) == 1


def test_failed_outbox_enqueue_rolls_back_report_photo_and_submission(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-rollback-royal")
    mechanic = _user(
        db_session, "mechanic", name="campaign-rollback-mechanic", park=seed_park_with_tracker
    )
    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    _seed_snapshot(db_session, campaign_id, seed_park_with_tracker.id)
    login_as(client, mechanic.username, "secret")

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("enqueue failed")

    monkeypatch.setattr(campaigns_svc, "begin_action", fail_enqueue)
    response = client.post(
        f"/campaigns/{campaign_id}/tickets/ROBOPARK-42/complete",
        data={"comment": "Готово", "park_id": str(seed_park_with_tracker.id)},
        files={"photo": ("done.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
    )
    assert response.status_code == 502
    assert db_session.scalars(select(Report)).all() == []
    assert db_session.scalars(select(ReportAttachment)).all() == []
    assert db_session.scalars(select(CampaignSubmission)).all() == []
    assert db_session.scalars(select(ReliableAction)).all() == []


def test_concurrent_same_key_completion_creates_one_result(
    client, db_engine, db_session, seed_park_with_tracker
):
    royal = _user(db_session, "royal", name="campaign-race-royal")
    mechanic = _user(
        db_session, "mechanic", name="campaign-race-mechanic", park=seed_park_with_tracker
    )
    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    _seed_snapshot(db_session, campaign_id, seed_park_with_tracker.id)
    factory = sessionmaker(bind=db_engine, future=True)
    mechanic_id = mechanic.id
    park_id = seed_park_with_tracker.id

    def complete(_index):
        with factory() as db:
            return campaigns_svc.complete_ticket(
                db,
                db.get(User, mechanic_id),
                campaign_id,
                "ROBOPARK-42",
                park_id=park_id,
                comment="Готово",
                filename="done.png",
                content=b"\x89PNG\r\n\x1a\n" + b"0" * 32,
                content_type="image/png",
                idempotency_key="same-race-001",
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(complete, range(2)))
    assert ids[0] == ids[1]
    db_session.expire_all()
    assert len(db_session.scalars(select(Report)).all()) == 1
    assert len(db_session.scalars(select(CampaignSubmission)).all()) == 1


def test_manager_deletes_empty_campaign_but_archives_one_with_result(
    client, db_session, seed_park_with_tracker
):
    royal = _user(db_session, "royal", name="campaign-delete-royal")
    mechanic = _user(
        db_session, "mechanic", name="campaign-delete-mechanic", park=seed_park_with_tracker
    )
    login_as(client, royal.username, "secret")
    empty_id = client.post("/campaigns", json=_campaign_payload(seed_park_with_tracker.id)).json()[
        "id"
    ]
    assert client.delete(f"/campaigns/{empty_id}").json() == {"result": "deleted"}
    assert db_session.get(Campaign, empty_id) is None

    kept_id = client.post("/campaigns", json=_campaign_payload(seed_park_with_tracker.id)).json()[
        "id"
    ]
    _seed_snapshot(db_session, kept_id, seed_park_with_tracker.id)
    login_as(client, mechanic.username, "secret")
    submitted = client.post(
        f"/campaigns/{kept_id}/tickets/ROBOPARK-42/complete",
        data={"comment": "Готово", "park_id": str(seed_park_with_tracker.id)},
        files={"photo": ("done.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
    )
    assert submitted.status_code == 201
    report_id = submitted.json()["report_id"]
    assert client.delete(f"/campaigns/{kept_id}").status_code == 403
    login_as(client, royal.username, "secret")
    assert client.delete(f"/campaigns/{kept_id}").json() == {"result": "archived"}
    assert db_session.get(Report, report_id) is not None
    assert kept_id not in {row["id"] for row in client.get("/campaigns").json()}


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
    assert campaigns_svc.process_refresh_batch(sessionmaker(bind=db_session.bind, future=True)) == 1

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
    assert campaigns_svc.process_refresh_batch(sessionmaker(bind=db_session.bind, future=True)) == 1

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
    db_engine,
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
    monkeypatch.setattr(tracker_outbox.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(tracker_outbox.tracker_client, "get_issue", lambda **kwargs: _issue())
    monkeypatch.setattr(
        tracker_outbox.tracker_client, "list_transitions", lambda **kwargs: transitions
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
    assert campaigns_svc.process_refresh_batch(sessionmaker(bind=db_engine, future=True)) == 1

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
    assert completed.json()["tracker_transition"] == "pending"
    assert transitioned == []
    assert tracker_outbox._process_batch(sessionmaker(bind=db_engine, future=True)) == 1
    db_session.expire_all()
    assert db_session.scalar(select(CampaignSubmission)).tracker_transition == expected_transition
    assert transitioned == [{"token": "token", "key": "ROBOPARK-42", "transition": expected_id}]

    submission = db_session.scalar(select(CampaignSubmission))
    report = db_session.get(Report, submission.report_id)
    assert report.kind == "campaign_review"
    assert report.target_role == "operator"
    assert report.park_id == seed_park_with_tracker.id
    assert report.tracker_key == "ROBOPARK-42"
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


def test_closed_tracker_snapshot_cannot_start_a_new_campaign_review(
    client, db_session, seed_park_with_tracker
):
    royal = _user(db_session, "royal", name="campaign-closed-owner")
    mechanic = _user(
        db_session, "mechanic", name="campaign-closed-mechanic", park=seed_park_with_tracker
    )
    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    db_session.add(
        CampaignSnapshotTicket(
            campaign_id=campaign_id,
            issue_key="ROBOPARK-42",
            park_id=seed_park_with_tracker.id,
            summary="A042 замена корпуса",
            status="Закрыт",
            status_key="closed",
            robot="42",
            rule_revision=1,
        )
    )
    db_session.commit()

    login_as(client, mechanic.username, "secret")
    response = client.post(
        f"/campaigns/{campaign_id}/tickets/ROBOPARK-42/complete",
        data={"comment": "Готово", "park_id": str(seed_park_with_tracker.id)},
        files={"photo": ("done.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
    )

    assert response.status_code == 409
    assert db_session.scalar(select(CampaignSubmission)) is None
    assert db_session.scalar(select(Report).where(Report.kind == "campaign_review")) is None


def test_completion_queues_tracker_transition_without_waiting_for_tracker(
    client, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-queue-admin")
    mechanic = _user(
        db_session, "mechanic", name="campaign-queue-worker", park=seed_park_with_tracker
    )
    monkeypatch.setattr(campaigns_svc.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(campaigns_svc.tracker_cache, "get_issue", lambda **kwargs: _issue())

    def unexpected_transition(**_kwargs):
        raise AssertionError("HTTP completion must not call Tracker transition")

    monkeypatch.setattr(campaigns_svc.tracker_cache, "list_transitions", unexpected_transition)
    monkeypatch.setattr(campaigns_svc.tracker_client, "transition_issue", unexpected_transition)

    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    _seed_snapshot(db_session, campaign_id, seed_park_with_tracker.id)
    login_as(client, mechanic.username, "secret")

    response = client.post(
        f"/campaigns/{campaign_id}/tickets/ROBOPARK-42/complete",
        data={"comment": "Оклейка завершена", "park_id": str(seed_park_with_tracker.id)},
        files={"photo": ("done.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
    )

    assert response.status_code == 201, response.text
    assert response.json()["tracker_transition"] == "pending"
    submission = db_session.scalar(select(CampaignSubmission))
    assert submission.tracker_transition == "pending"
    action = db_session.scalar(select(ReliableAction))
    assert action.resource_id == "ROBOPARK-42"
    assert action.action == "campaign_review"
    assert action.state == "pending"


def test_cancelled_campaign_submission_does_not_transition_tracker(
    client, db_engine, db_session, seed_park_with_tracker, monkeypatch
):
    royal = _user(db_session, "royal", name="campaign-cancel-admin")
    mechanic = _user(
        db_session, "mechanic", name="campaign-cancel-worker", park=seed_park_with_tracker
    )
    monkeypatch.setattr(campaigns_svc.settings_svc, "get_tracker_token", lambda db: "token")
    monkeypatch.setattr(campaigns_svc.tracker_cache, "get_issue", lambda **kwargs: _issue())
    monkeypatch.setattr(tracker_outbox.tracker_client, "get_issue", lambda **kwargs: _issue())
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "list_transitions",
        lambda **kwargs: [{"id": "review", "display": "Проверка"}],
    )
    transitions = []
    monkeypatch.setattr(
        tracker_outbox.tracker_client,
        "transition_issue",
        lambda **kwargs: transitions.append(kwargs),
    )

    login_as(client, royal.username, "secret")
    campaign_id = client.post(
        "/campaigns", json=_campaign_payload(seed_park_with_tracker.id)
    ).json()["id"]
    _seed_snapshot(db_session, campaign_id, seed_park_with_tracker.id)
    login_as(client, mechanic.username, "secret")
    response = client.post(
        f"/campaigns/{campaign_id}/tickets/ROBOPARK-42/complete",
        data={"comment": "Оклейка завершена", "park_id": str(seed_park_with_tracker.id)},
        files={"photo": ("done.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
    )
    assert response.status_code == 201
    submission = db_session.scalar(select(CampaignSubmission))
    db_session.delete(submission)
    db_session.commit()

    assert tracker_outbox._process_batch(sessionmaker(bind=db_engine, future=True)) == 1
    db_session.expire_all()
    assert transitions == []
    assert db_session.scalar(select(ReliableAction)).state == "succeeded"
