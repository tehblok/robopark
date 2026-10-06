from types import SimpleNamespace

import httpx
import pytest

from robopark_api.services.ai import runtime


@pytest.mark.parametrize(
    "status,upstream,expected",
    [
        (429, "ai_queue_full", "ai_queue_full"),
        (503, "ai_queue_timeout", "ai_runtime_busy"),
        (422, "context_limit", "ai_context_too_large"),
        (503, "ai_unavailable", "ai_runtime_unavailable"),
    ],
)
def test_broker_distinguishes_overload_from_failed_runtime(monkeypatch, status, upstream, expected):
    real_client = httpx.Client
    monkeypatch.setattr(
        runtime.httpx,
        "HTTPTransport",
        lambda **kwargs: httpx.MockTransport(
            lambda request: httpx.Response(status, json={"error": upstream})
        ),
    )
    monkeypatch.setattr(runtime.httpx, "Client", real_client)
    with pytest.raises(runtime.RuntimeFailure, match=f"^{expected}$"):
        runtime.broker(SimpleNamespace(ai_broker_socket="/unused.sock"), "/v1/chat/completions", {})
