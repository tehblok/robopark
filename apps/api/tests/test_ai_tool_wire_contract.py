"""Actual role catalogs and fitted history survive the private host protocol."""

import json
from pathlib import Path

import pytest

from robopark_api.services.ai import prompts, tool_domain


@pytest.mark.parametrize("role_fixture", ["seed_admin", "seed_mechanic"])
def test_real_catalog_and_fitted_history_are_accepted_by_host(
    db_session, request, role_fixture, monkeypatch
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "deploy/host"))
    from robopark_host.ai_broker import bounded_chat_body

    user = request.getfixturevalue(role_fixture)
    definitions = tool_domain.catalog(db_session, user)
    messages, _ = prompts.fit_context(
        db_session,
        user,
        [],
        "Проверь задачу",
        history=[{"role": "assistant", "content": "Старый ответ"}],
        tools=True,
        token_count=lambda _: (100, 8192),
    )
    value = json.loads(
        bounded_chat_body(json.dumps({"messages": messages, "tools": definitions}).encode())
    )
    assert [message["role"] for message in value["messages"]] == ["system", "user"]
    assert value["parallel_tool_calls"] is False
    assert {tool["function"]["name"] for tool in value["tools"]} == {
        tool["function"]["name"] for tool in definitions
    }
