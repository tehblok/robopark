from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

import requests


class ServiceError(RuntimeError):
    """Only bounded, non-sensitive error codes cross the transport boundary."""

    def __init__(self, code: str, *, status: int = 0, uncertain: bool = False):
        self.code = code if re.fullmatch(r"[a-z0-9_]{1,80}", code) else "service_error"
        self.status = status
        self.uncertain = uncertain
        super().__init__(self.code)


def _read_json(response, *, limit=8 * 1024 * 1024):
    raw = bytearray()
    for chunk in response.iter_content(65536):
        raw.extend(chunk)
        if len(raw) > limit:
            raise ServiceError("response_too_large", uncertain=True)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise ServiceError("invalid_service_response", uncertain=True) from None


class APIClient:
    def __init__(self, base_url: str, key: str, *, timeout=(5, 45)):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("invalid_robopark_api_url")
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.timeout = timeout
        self.session = requests.Session()

    def call(self, method: str, path: str, payload=None):
        if (
            not path.startswith("/")
            or path.startswith("//")
            or any(c in path for c in "\r\n")
        ):
            raise ValueError("invalid_native_api_path")
        try:
            with self.session.request(
                method,
                self.base_url + "/internal/bot/native" + path,
                headers={"X-Robopark-Bot-Key": self.key},
                json=payload,
                allow_redirects=False,
                stream=True,
                timeout=self.timeout,
            ) as response:
                if response.status_code == 204:
                    return None
                data = _read_json(response)
                if not 200 <= response.status_code < 300:
                    detail = data.get("detail") if isinstance(data, dict) else None
                    raise ServiceError(
                        detail
                        if isinstance(detail, str)
                        else "robopark_request_failed",
                        status=response.status_code,
                    )
                return data
        except requests.RequestException:
            raise ServiceError("robopark_unavailable") from None

    def close(self):
        self.session.close()


class TelegramClient:
    METHODS = frozenset(
        {"getMe", "getUpdates", "sendMessage", "sendPhoto", "answerCallbackQuery"}
    )

    def __init__(self, token: str):
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
            raise ValueError("invalid_telegram_token")
        self.base_url = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()

    def call(self, method: str, payload=None, *, photo: bytes | None = None):
        if method not in self.METHODS:
            raise ValueError("unsupported_telegram_method")
        payload = payload or {}
        if photo is not None and len(photo) > 9 * 1024 * 1024:
            raise ServiceError("report_image_too_large")
        options = (
            {"json": payload}
            if photo is None
            else {
                "data": {
                    k: json.dumps(v) if isinstance(v, (dict, list)) else v
                    for k, v in payload.items()
                },
                "files": {"photo": ("report.png", photo, "image/png")},
            }
        )
        try:
            for attempt in range(2):
                with self.session.post(
                    self.base_url + method,
                    **options,
                    allow_redirects=False,
                    stream=True,
                    timeout=(5, 40),
                ) as response:
                    data = _read_json(response)
                    if (
                        response.status_code == 200
                        and isinstance(data, dict)
                        and data.get("ok") is True
                    ):
                        return data.get("result")
                    status = response.status_code
                    parameters = (
                        data.get("parameters", {}) if isinstance(data, dict) else {}
                    )
                    delay = (
                        parameters.get("retry_after")
                        if isinstance(parameters, dict)
                        else None
                    )
                    # Only an explicit rate-limit rejection proves no send occurred.
                    # Bound both attempts and delay within the delivery lease.
                    retry = (
                        status == 429
                        and isinstance(data, dict)
                        and data.get("ok") is False
                        and attempt == 0
                        and type(delay) is int
                        and 0 <= delay <= 30
                    )
                    if not retry:
                        raise ServiceError(
                            f"telegram_http_{status}",
                            status=status,
                            uncertain=status >= 500,
                        )
                time.sleep(delay)
        except requests.RequestException:
            # A lost response does not establish whether Telegram accepted the message.
            raise ServiceError("telegram_transport_error", uncertain=True) from None

    def close(self):
        self.session.close()
