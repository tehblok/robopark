import time

from sqlalchemy import select

from conftest import login_as
from robopark_api.ai_models import AIConversation, AIJob, AIMessage, AIRun, AIScript
from test_ai import enable_host


def test_history_cleanup_removes_completed_standalone_jobs_and_preserves_live_work(
    client, db_session, seed_admin, seed_park_with_tracker, test_settings, tmp_path
):
    enable_host(test_settings, tmp_path)
    old = time.time() - 40 * 86400
    recent = time.time()
    owner = seed_admin.id
    park = seed_park_with_tracker.id
    for identity in ("old-idle", "old-busy", "recent"):
        db_session.add(
            AIConversation(
                id=identity,
                owner_id=owner,
                park_id=park,
                updated_at=recent if identity == "recent" else old,
            )
        )
    db_session.flush()
    for identity in ("old-idle", "old-busy", "recent"):
        db_session.add(AIMessage(conversation_id=identity, role="user", content="text"))
    cases = [
        ("old-chat", "chat", "succeeded", "old-idle", old),
        ("busy-chat", "chat", "queued", "old-busy", old),
        ("old-script", "script_test", "succeeded", None, old),
        ("old-draft", "draft", "succeeded", None, old),
        ("old-failed", "script_test", "failed", None, old),
        ("old-cancelled", "draft", "cancelled", None, old),
        ("running", "script_test", "running", None, old),
        ("queued", "draft", "queued", None, old),
        ("recent-draft", "draft", "succeeded", None, recent),
    ]
    for identity, kind, state, conversation, updated_at in cases:
        db_session.add(
            AIJob(
                id=identity,
                owner_id=owner,
                kind=kind,
                state=state,
                conversation_id=conversation,
                idempotency_key=identity,
                updated_at=updated_at,
                payload={"request": {"input": "old private input"}},
                result={"output": "old private result"},
            )
        )
    db_session.add(AIScript(id="script", name="keep", source="def main(data): return data"))
    db_session.add(
        AIRun(id="receipt", automation_id="rule", event_key="event", revision=1, state="uncertain")
    )
    db_session.commit()
    login_as(client, "admin", "secret")

    response = client.post("/ai/maintenance", json={"kind": "history", "before_days": 30})

    assert response.status_code == 200
    db_session.expire_all()
    assert set(db_session.scalars(select(AIJob.id))) == {
        "busy-chat",
        "running",
        "queued",
        "recent-draft",
    }
    assert set(db_session.scalars(select(AIConversation.id))) == {"old-busy", "recent"}
    assert set(db_session.scalars(select(AIMessage.conversation_id))) == {"old-busy", "recent"}
    assert db_session.get(AIScript, "script") is not None
    assert db_session.get(AIRun, "receipt").state == "uncertain"
    assert response.json() == {
        "deleted": 1,
        "conversations_deleted": 1,
        "jobs_deleted": 5,
        "messages_deleted": 1,
    }
    repeated = client.post("/ai/maintenance", json={"kind": "history", "before_days": 30})
    assert repeated.json() == {
        "deleted": 0,
        "conversations_deleted": 0,
        "jobs_deleted": 0,
        "messages_deleted": 0,
    }


def test_failed_job_cleanup_does_not_remove_successful_or_active_jobs(
    client, db_session, seed_admin, test_settings, tmp_path
):
    enable_host(test_settings, tmp_path)
    old = time.time() - 40 * 86400
    for state in ("succeeded", "failed", "cancelled", "queued", "running"):
        db_session.add(
            AIJob(
                id=state,
                owner_id=seed_admin.id,
                kind="draft",
                state=state,
                idempotency_key=state,
                updated_at=old,
            )
        )
    db_session.commit()
    login_as(client, "admin", "secret")

    response = client.post("/ai/maintenance", json={"kind": "failed_jobs", "before_days": 30})

    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    db_session.expire_all()
    assert set(db_session.scalars(select(AIJob.id))) == {"succeeded", "queued", "running"}
