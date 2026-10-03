"""Telegram admin cannot create a second Tracker credential store."""

from __future__ import annotations

import importlib
from pathlib import Path


APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_telegram_tracker_token_action_points_to_main_site(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    system = importlib.import_module("admin.system")
    monkeypatch.setattr(system, "is_full_admin", lambda _user_id: True)
    messages = []

    assert system.handle_sys_callback(
        user_id=1,
        parts=["adm", "sys", "tok_tr"],
        callback_id="test",
        send=lambda _user_id, message, **_kwargs: messages.append(message),
        answer=lambda *_args, **_kwargs: None,
    )
    assert messages and "Robopark" in messages[0]
    assert system.get_session(1) is None


def test_legacy_store_cannot_persist_separate_tracker_token(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    secrets_store = importlib.import_module("store.secrets")
    assert not hasattr(secrets_store, "set_tracker_token")
    assert not hasattr(secrets_store, "get_tracker_token")


def test_telegram_credential_is_supplied_only_by_primary_site_at_runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    secrets_store = importlib.import_module("store.secrets")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(
        secrets_store, "_read_env_file",
        lambda _path: {"TELEGRAM_BOT_TOKEN": "legacy-credential"},
        raising=False,
    )

    assert secrets_store.get_telegram_bot_token() == ""
    assert not hasattr(secrets_store, "set_telegram_bot_token")
