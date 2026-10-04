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


def context_tokens(settings, messages):
    value = broker(
        settings,
        "/v1/chat/tokens",
        {"messages": messages, "max_tokens": 1400},
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
