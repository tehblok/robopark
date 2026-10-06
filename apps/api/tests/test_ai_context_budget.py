import json
from types import SimpleNamespace

import pytest

from robopark_api.ai_models import AIPrompt
from robopark_api.services.ai import prompts, runtime


class PromptDB:
    def __init__(self, guidance):
        self.guidance = guidance

    def get(self, model, key):
        assert model is AIPrompt
        return SimpleNamespace(content=self.guidance) if key == "mechanic" else None


def test_runtime_context_tokens_uses_read_only_broker_operation(monkeypatch):
    seen = []

    def broker(settings, path, payload, *, timeout):
        seen.append((settings, path, payload, timeout))
        return {"prompt_tokens": 321, "context_tokens": 8192}

    monkeypatch.setattr(runtime, "broker", broker)
    settings = object()
    messages = [{"role": "user", "content": "hello"}]

    assert runtime.context_tokens(settings, messages) == (321, 8192)
    assert seen == [
        (
            settings,
            "/v1/chat/tokens",
            {"messages": messages, "max_tokens": 1400},
            30,
        )
    ]


def test_fit_context_uses_model_tokens_to_retain_three_coherent_sources():
    db = PromptDB("role guidance " * 1000)
    user = SimpleNamespace(role="mechanic")
    question = "Как безопасно проверить камеру? " + "важно " * 80
    sources = [
        {
            "id": f"source-{index}",
            "title": f"Источник {index}",
            "kind": "manual",
            "trust": "instruction",
            "excerpt": (f"выдержка {index} " * 240),
            "revision": 3,
        }
        for index in range(3)
    ]

    messages, fitted_sources = prompts.fit_context(
        db,
        user,
        sources,
        question,
        issue="тикет " * 800,
        history=[{"role": "assistant", "content": "история " * 500}],
        token_count=lambda messages: (
            sum(len(message["content"]) for message in messages) // 3,
            8192,
        ),
    )

    assert sum(len(message["content"]) for message in messages) // 3 + 1400 <= 8192
    assert messages[-1] == {"role": "user", "content": question}
    assert prompts.POLICY in messages[0]["content"]
    assert prompts.TRUNCATION_MARKER.strip() in messages[0]["content"]
    encoded_sources = messages[0]["content"].split(prompts.SOURCES_HEADER, 1)[1]
    assert json.loads(encoded_sources) == fitted_sources
    assert fitted_sources
    assert len(fitted_sources) == 3
    assert fitted_sources == sources
    assert all(len(source["excerpt"]) >= 2000 for source in fitted_sources)


def test_fit_context_fallback_bounds_bytes_and_returns_exactly_rendered_sources():
    sources = [
        {"id": f"source-{index}", "excerpt": "данные " * 1000, "revision": 1} for index in range(3)
    ]

    messages, fitted_sources = prompts.fit_context(
        PromptDB("short"),
        SimpleNamespace(role="mechanic"),
        sources,
        "Короткий вопрос",
    )

    assert (
        sum(len(message["content"].encode("utf-8")) for message in messages)
        <= prompts.CONTEXT_BYTES
    )
    encoded_sources = messages[0]["content"].split(prompts.SOURCES_HEADER, 1)[1]
    assert json.loads(encoded_sources) == fitted_sources
    assert any(
        source["excerpt"] != original["excerpt"]
        for source, original in zip(fitted_sources, sources, strict=False)
    )
    assert all(prompts.TRUNCATION_MARKER.strip() in source["excerpt"] for source in fitted_sources)
    assert all(source["excerpt"].strip() for source in fitted_sources)


def test_fit_context_rechecks_mandatory_context_when_native_counter_fails():
    calls = 0

    def flaky_counter(_messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 1000, 8192
        raise runtime.RuntimeFailure("ai_runtime_unavailable")

    with pytest.raises(prompts.ContextTooLarge):
        prompts.fit_context(
            PromptDB("guidance " * 1000),
            SimpleNamespace(role="mechanic"),
            [{"id": "source", "excerpt": "данные " * 1000, "revision": 1}],
            "вопрос " * 500,
            token_count=flaky_counter,
        )


def test_fit_context_rejects_question_that_cannot_fit_with_fixed_policy():
    with pytest.raises(prompts.ContextTooLarge):
        prompts.fit_context(
            PromptDB("short"),
            SimpleNamespace(role="mechanic"),
            [],
            "вопрос " * prompts.CONTEXT_BYTES,
        )


def test_empty_knowledge_does_not_add_source_contract_or_require_citations():
    messages, sources = prompts.fit_context(
        PromptDB("Помогай механику."),
        SimpleNamespace(role="mechanic"),
        [],
        "Что проверить?",
    )

    assert sources == []
    assert prompts.SOURCES_HEADER not in messages[0]["content"]
    assert "Если источников нет, прямо скажи это" not in messages[0]["content"]
    assert "Ссылайся только" not in messages[0]["content"]


def test_complete_knowledge_articles_are_kept_whole_or_omitted():
    header = "База знаний Robopark\nОбласть применения: R3.9\n"
    sources = [
        {
            "id": "too-long",
            "excerpt": header + "Техническое основание. " * 400 + "\nЗамена НЕ помогла.",
        },
        {"id": "fits", "excerpt": header + "Отключить обе АКБ.\nПроверили.\nОшибка осталась."},
    ]
    messages, fitted = prompts.fit_context(
        PromptDB(""), SimpleNamespace(role="mechanic"), sources, "Что известно?"
    )
    assert fitted == [sources[1]]
    assert (
        sources[1]["excerpt"]
        in json.loads(messages[0]["content"].split(prompts.SOURCES_HEADER)[1])[0]["excerpt"]
    )
    assert prompts.TRUNCATION_MARKER not in messages[0]["content"]
