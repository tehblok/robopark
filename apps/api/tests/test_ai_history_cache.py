"""History rendering shares authorization work only within one HTTP request."""

from conftest import login_as
from robopark_api.ai_models import AIConversation, AIJob, AIMessage
from robopark_api.services.ai import tool_actions
from test_ai import enable_host


def test_conversation_shares_scope_checks_but_never_caches_across_requests(
    client, db_session, seed_admin, seed_park_with_tracker, test_settings, tmp_path, monkeypatch
):
    enable_host(test_settings, tmp_path)
    login_as(client, "admin", "secret")
    conversation = AIConversation(owner_id=seed_admin.id, park_id=seed_park_with_tracker.id)
    db_session.add(conversation)
    db_session.flush()
    message = AIMessage(
        conversation_id=conversation.id, role="assistant", content="Проверка готова"
    )
    db_session.add(message)
    db_session.flush()
    db_session.add(
        AIJob(
            owner_id=seed_admin.id,
            park_id=seed_park_with_tracker.id,
            conversation_id=conversation.id,
            kind="chat",
            state="succeeded",
            payload={"request": {"use_tools": True}},
            result={"message_id": message.id},
            idempotency_key="history-scope-test",
        )
    )
    db_session.commit()
    seen = []

    def authorize(_db, _user, _job, *, scope_cache=None):
        assert scope_cache is not None
        assert scope_cache["proof"] is True
        seen.append(scope_cache)

    def views(_db, _user, _job, *, scope_cache=None):
        assert scope_cache is not None and "proof" not in scope_cache
        scope_cache["proof"] = True
        return []

    monkeypatch.setattr(tool_actions, "authorize_views", authorize)
    monkeypatch.setattr(tool_actions, "views", views)
    first = client.get(f"/ai/conversations/{conversation.id}")
    second = client.get(f"/ai/conversations/{conversation.id}")
    assert first.status_code == second.status_code == 200
    assert len(seen) == 2 and seen[0] is not seen[1]
