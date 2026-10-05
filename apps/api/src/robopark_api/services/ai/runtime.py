"""Bounded private transport. There is deliberately no cloud/CPU fallback."""

import json

import httpx


class RuntimeFailure(Exception):
    pass


def broker(settings, path, payload=None, *, timeout=120):
    if path not in {
        "/health",
        "/v1/chat/completions",
        "/v1/chat/tokens",
        "/sandbox",
        "/control",
    }:
        raise RuntimeFailure("ai_invalid_operation")
    try:
        transport = httpx.HTTPTransport(uds=settings.ai_broker_socket, retries=0)
        with (
            httpx.Client(
                transport=transport,
                base_url="http://localhost",
                timeout=timeout,
                trust_env=False,
                follow_redirects=False,
            ) as client,
            client.stream("GET" if payload is None else "POST", path, json=payload) as response,
        ):
            status_code = response.status_code
            body = bytearray()
            for part in response.iter_bytes():
                body.extend(part)
                if len(body) > 262144:
                    raise RuntimeFailure("ai_response_too_large")
        value = json.loads(body)
        if status_code != 200:
            code = (
                "ai_context_too_large"
                if isinstance(value, dict) and value.get("error") == "context_limit"
                else "ai_runtime_unavailable"
            )
            raise RuntimeFailure(code)
        if not isinstance(value, dict):
            raise RuntimeFailure("ai_invalid_response")
        return value
    except (httpx.HTTPError, OSError, ValueError) as exc:
        raise RuntimeFailure("ai_runtime_unavailable") from exc


def context_tokens(settings, messages, *, tools=None):
    payload = {"messages": messages, "max_tokens": 1400}
    if tools is not None:
        payload["tools"] = tools
    value = broker(
        settings,
        "/v1/chat/tokens",
        payload,
        timeout=30,
    )
    prompt_tokens = value.get("prompt_tokens")
    context_tokens = value.get("context_tokens")
    if (
        type(prompt_tokens) is not int
        or prompt_tokens < 0
        or type(context_tokens) is not int
        or context_tokens < 1
    ):
        raise RuntimeFailure("ai_invalid_response")
    return prompt_tokens, context_tokens


def _strict_object(value):
    def invalid(_value):
        raise ValueError("non_finite_json")

    parsed = json.loads(value, parse_constant=invalid)
    if not isinstance(parsed, dict):
        raise ValueError("arguments_not_object")
    return parsed


def complete_turn(settings, messages, tools):
    value = broker(
        settings,
        "/v1/chat/completions",
        {
            "messages": messages,
            "tools": tools,
            "max_tokens": 1400,
            "temperature": 0.2,
            "stream": False,
            "reasoning_effort": "none",
        },
        timeout=180,
    )
    try:
        choices = value["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("choice_count")
        choice = choices[0]
        message = choice["message"]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValueError("message")
        finish_reason = choice.get("finish_reason")
        content = message.get("content")
        if finish_reason == "stop":
            if (
                not isinstance(content, str)
                or not content.strip()
                or len(content) > 20000
                or message.get("tool_calls")
            ):
                raise ValueError("invalid_stop")
            return {"role": "assistant", "content": content.strip()}
        if finish_reason != "tool_calls":
            raise ValueError("incomplete")
        if content is not None and not isinstance(content, str):
            raise ValueError("content")
        if isinstance(content, str) and len(content) > 20000:
            raise ValueError("content")
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            raise ValueError("tool_call_count")
        call = calls[0]
        function = call["function"]
        call_id = call["id"]
        name = function["name"]
        arguments = function["arguments"]
        prior_call_ids = {
            prior_call.get("id")
            for prior_message in messages
            if isinstance(prior_message, dict)
            for prior_call in prior_message.get("tool_calls", [])
            if isinstance(prior_call, dict)
        }
        known_names = {
            tool["function"]["name"]
            for tool in tools
            if isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and isinstance(tool["function"].get("name"), str)
        }
        if (
            not isinstance(call, dict)
            or call.get("type") != "function"
            or not isinstance(function, dict)
            or not isinstance(call_id, str)
            or not 1 <= len(call_id) <= 128
            or call_id in prior_call_ids
            or not isinstance(name, str)
            or not 1 <= len(name) <= 64
            or name not in known_names
            or not isinstance(arguments, str)
            or len(arguments.encode("utf-8")) > 16384
        ):
            raise ValueError("tool_call")
        parsed_arguments = _strict_object(arguments)
        return {
            "role": "assistant",
            "content": "" if content is None else content.strip(),
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            parsed_arguments, ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                }
            ],
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeFailure("ai_incomplete_response") from exc


def complete(settings, messages):
    value = broker(
        settings,
        "/v1/chat/completions",
        {
            "messages": messages,
            "max_tokens": 1400,
            "temperature": 0.2,
            "stream": False,
            "reasoning_effort": "none",
        },
        timeout=180,
    )
    try:
        choice = value["choices"][0]
        content = choice["message"]["content"]
        if (
            choice.get("finish_reason") != "stop"
            or not isinstance(content, str)
            or not content.strip()
        ):
            raise ValueError("incomplete")
        if len(content) > 20000 or choice["message"].get("tool_calls"):
            raise ValueError("invalid")
        return content.strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeFailure("ai_incomplete_response") from exc
