import pytest

from robopark_api.services.ai import runtime

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_robot",
            "description": "Read one robot",
            "parameters": {
                "type": "object",
                "properties": {"robot_id": {"type": "integer"}},
                "required": ["robot_id"],
            },
        },
    }
]


def test_complete_turn_normalizes_one_known_function_call(monkeypatch):
    seen = []

    def broker(settings, path, payload, *, timeout):
        seen.append((settings, path, payload, timeout))
        return {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_robot",
                                    "arguments": '{ "robot_id": 7 }',
                                },
                            }
                        ],
                    },
                }
            ]
        }

    monkeypatch.setattr(runtime, "broker", broker)
    settings = object()
    messages = [{"role": "user", "content": "Покажи робота 7"}]

    assert runtime.complete_turn(settings, messages, TOOLS) == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_robot", "arguments": '{"robot_id":7}'},
            }
        ],
    }
    assert seen == [
        (
            settings,
            "/v1/chat/completions",
            {
                "messages": messages,
                "tools": TOOLS,
                "max_tokens": 1400,
                "temperature": 0.2,
                "stream": False,
                "reasoning_effort": "none",
            },
            180,
        )
    ]


def test_complete_turn_normalizes_text_stop(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "broker",
        lambda *_args, **_kwargs: {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "  Готово  "},
                }
            ]
        },
    )

    assert runtime.complete_turn(object(), [{"role": "user", "content": "да"}], TOOLS) == {
        "role": "assistant",
        "content": "Готово",
    }


@pytest.mark.parametrize(
    "choice",
    [
        {
            "finish_reason": "length",
            "message": {"role": "assistant", "content": "partial"},
        },
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "unknown", "arguments": "{}"},
                    }
                ],
            },
        },
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_robot", "arguments": "[]"},
                    }
                ],
            },
        },
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_robot", "arguments": "{}"},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "get_robot", "arguments": "{}"},
                    },
                ],
            },
        },
        {
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "x" * 129,
                        "type": "function",
                        "function": {"name": "get_robot", "arguments": "{}"},
                    }
                ],
            },
        },
    ],
    ids=[
        "length-finish",
        "unknown-tool",
        "array-arguments",
        "parallel-calls",
        "oversized-call-id",
    ],
)
def test_complete_turn_rejects_incomplete_or_malformed_choices(monkeypatch, choice):
    monkeypatch.setattr(
        runtime,
        "broker",
        lambda *_args, **_kwargs: {"choices": [choice]},
    )

    with pytest.raises(runtime.RuntimeFailure, match="ai_incomplete_response"):
        runtime.complete_turn(object(), [{"role": "user", "content": "да"}], TOOLS)


def test_complete_turn_rejects_more_than_one_choice(monkeypatch):
    choice = {
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": "ok"},
    }
    monkeypatch.setattr(
        runtime,
        "broker",
        lambda *_args, **_kwargs: {"choices": [choice, choice]},
    )

    with pytest.raises(runtime.RuntimeFailure, match="ai_incomplete_response"):
        runtime.complete_turn(object(), [{"role": "user", "content": "да"}], TOOLS)


def test_complete_turn_rejects_tool_call_id_reused_from_history(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "broker",
        lambda *_args, **_kwargs: {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "get_robot", "arguments": "{}"},
                            }
                        ],
                    },
                }
            ]
        },
    )
    messages = [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_robot", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ]

    with pytest.raises(runtime.RuntimeFailure, match="ai_incomplete_response"):
        runtime.complete_turn(object(), messages, TOOLS)


def test_context_tokens_includes_tools_in_broker_payload(monkeypatch):
    seen = []

    def broker(_settings, _path, payload, *, timeout):
        seen.append((payload, timeout))
        return {"prompt_tokens": 42, "context_tokens": 8192}

    monkeypatch.setattr(runtime, "broker", broker)
    messages = [{"role": "user", "content": "hello"}]

    assert runtime.context_tokens(object(), messages, tools=TOOLS) == (42, 8192)
    assert seen == [({"messages": messages, "tools": TOOLS, "max_tokens": 1400}, 30)]
