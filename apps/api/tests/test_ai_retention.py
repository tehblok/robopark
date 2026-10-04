"""Old event bodies expire without losing delivery/learning deduplication."""

from sqlalchemy import select

from conftest import login_as
from robopark_api.ai_models import AIDocument, AIEvent, AIRun
from robopark_api.services.ai import learning
from test_ai import enable_host


def test_event_cleanup_keeps_pending_unprocessed_and_recent_payloads(
    db_session, seed_park_with_tracker
):
    park = seed_park_with_tracker.id
    for key, processed, occurred in (
        ("finished", True, 1),
        ("no-rules", True, 2),
        ("queued", True, 3),
        ("running", True, 4),
        ("unprocessed", False, 5),
        ("recent", True, 500),
    ):
        db_session.add(
            AIEvent(
                key=key,
                park_id=park,
                processed=processed,
                occurred_at=occurred,
                payload={"issue_key": "R-1", "comment": "private repair text"},
            )
        )
    for key, state in (("finished", "uncertain"), ("queued", "queued"), ("running", "running")):
        db_session.add(AIRun(automation_id="rule", event_key=key, revision=1, state=state))
    db_session.commit()
    assert learning.purge_event_payloads(db_session, before=100, limit=1) == 1
    db_session.commit()
    assert learning.purge_event_payloads(db_session, before=100, limit=1) == 1
    db_session.commit()
    assert learning.purge_event_payloads(db_session, before=100) == 0
    rows = {r.key: r for r in db_session.scalars(select(AIEvent))}
    for key in ("finished", "no-rules"):
        assert rows[key].payload == {} and not rows[key].payload_retained
        assert rows[key].processed
    for key in ("queued", "running", "unprocessed", "recent"):
        assert rows[key].payload["comment"] == "private repair text"
    assert len(list(db_session.scalars(select(AIRun)))) == 3
    run = db_session.scalar(select(AIRun).where(AIRun.event_key == "queued"))
    run.state = "succeeded"
    db_session.commit()
    assert learning.purge_event_payloads(db_session, before=100) == 1


def test_user_history_cleanup_scrubs_completed_event_but_preserves_learned_document(
    client, db_session, seed_admin, seed_park_with_tracker, test_settings, tmp_path
):
    enable_host(test_settings, tmp_path)
    event = AIEvent(
        key="old-repair",
        park_id=seed_park_with_tracker.id,
        occurred_at=1,
        payload={"issue_key": "R-1", "comment": "Confirmed repair"},
    )
    db_session.add(event)
    db_session.commit()
    learning.process_events(db_session, test_settings)
    doc = db_session.scalar(select(AIDocument))
    assert "Confirmed repair" in doc.content
    db_session.add(
        AIRun(
            automation_id="rule",
            event_key=event.key,
            revision=1,
            state="succeeded",
            created_at=1,
            result={"private": "old output"},
        )
    )
    db_session.commit()
    login_as(client, "admin", "secret")
    response = client.delete("/ai/runs?before_days=1")
    assert response.status_code == 200
    assert response.json()["events_cleaned"] == 1
    db_session.expire_all()
    assert db_session.get(AIEvent, event.key).payload == {}
    run = db_session.scalar(select(AIRun))
    assert run.state == "purged" and run.result is None
    assert "Confirmed repair" in db_session.get(AIDocument, doc.id).content


def test_worker_automatically_scrubs_only_after_agx_gate(
    db_session, db_engine, seed_park_with_tracker, test_settings, tmp_path
):
    from sqlalchemy.orm import sessionmaker

    from robopark_api.services.ai import jobs

    db_session.add(
        AIEvent(
            key="worker-old",
            park_id=seed_park_with_tracker.id,
            processed=True,
            occurred_at=1,
            payload={"comment": "obsolete input"},
        )
    )
    db_session.commit()
    factory = sessionmaker(bind=db_engine)
    assert jobs.tick(factory, test_settings) is False
    db_session.expire_all()
    assert db_session.get(AIEvent, "worker-old").payload
    enable_host(test_settings, tmp_path)
    assert jobs.tick(factory, test_settings)
    db_session.expire_all()
    assert db_session.get(AIEvent, "worker-old").payload == {}
