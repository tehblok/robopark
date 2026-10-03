"""Read the Telegram token injected by Robopark and redact network errors."""

from __future__ import annotations

import os
import re

KEY_TELEGRAM = "TELEGRAM_BOT_TOKEN"
_TOKEN_IN_URL = re.compile(r"/bot[^/\s?]+(?=/|[?\s]|$)")


def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-keep:]}"


def get_telegram_bot_token() -> str:
    return os.environ.get(KEY_TELEGRAM, "").strip()


def redact_url(url: str) -> str:
    return _TOKEN_IN_URL.sub("/bot***", url)


def safe_error(error: BaseException | str) -> str:
    """Bound exception text and remove the active Telegram credential."""
    message = str(error)[:1000]
    token = get_telegram_bot_token()
    if token:
        message = message.replace(token, "***")
    return redact_url(message)
