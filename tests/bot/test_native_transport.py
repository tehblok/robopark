import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/bot"))
from native.transport import APIClient, ServiceError, TelegramClient


class Response:
    def __init__(self, status, data):
        self.status_code, self.data = status, data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def iter_content(self, *_):
        yield json.dumps(self.data).encode()


def test_explicit_telegram_rate_limit_is_retried_once_without_losing_payload(
    monkeypatch,
):
    tg = TelegramClient("123:TEST_ONLY")
    calls, waits = [], []
    responses = iter(
        [
            Response(429, {"ok": False, "parameters": {"retry_after": 3}}),
            Response(200, {"ok": True, "result": {"message_id": 12}}),
        ]
    )
    monkeypatch.setattr("native.transport.time.sleep", waits.append)

    def post(url, **kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(tg.session, "post", post)
    assert tg.call(
        "sendPhoto", {"chat_id": -1001, "message_thread_id": 42}, photo=b"PNG"
    ) == {"message_id": 12}
    assert waits == [3]
    assert calls[0] == calls[1]
    assert calls[0]["allow_redirects"] is False
    tg.close()


def test_unknown_telegram_transport_result_never_retries_or_exposes_token(monkeypatch):
    tg = TelegramClient("123:TEST_ONLY")
    calls = []

    def post(*args, **kwargs):
        calls.append(args)
        raise requests.Timeout("https://api.telegram.org/bot123:TEST_ONLY/sendMessage")

    monkeypatch.setattr(tg.session, "post", post)
    with pytest.raises(ServiceError) as caught:
        tg.call("sendMessage", {"chat_id": 1, "text": "message"})
    assert len(calls) == 1
    assert caught.value.uncertain
    assert "TEST_ONLY" not in str(caught.value)
    tg.close()


def test_native_api_caps_streamed_payload_and_disallows_redirects(monkeypatch):
    api = APIClient("http://api:8000", "private-test-key")
    response = Response(200, {})
    response.iter_content = lambda *_: iter([b"x" * (8 * 1024 * 1024 + 1)])
    observed = []

    def request(*args, **kwargs):
        observed.append(kwargs)
        return response

    monkeypatch.setattr(api.session, "request", request)
    with pytest.raises(ServiceError, match="response_too_large"):
        api.call("GET", "/context?telegram_user_id=1")
    assert observed[0]["allow_redirects"] is False
    api.close()
