"""Network exceptions may contain Telegram bot credentials in request URLs."""

from __future__ import annotations

import importlib
import secrets
from pathlib import Path


APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_telegram_token_is_removed_from_exception_text(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    store = importlib.import_module("store.secrets")
    token = f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(24)}"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    message = f"request failed at https://api.telegram.org/bot{token}/sendPhoto"
    safe = store.safe_error(message)
    assert token not in safe
    assert "sendPhoto" in safe
