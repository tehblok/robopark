"""A clean installation must not grant bot privileges to historic accounts."""

from __future__ import annotations

import importlib
from pathlib import Path


APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_clean_install_has_no_implicit_telegram_admin_or_user(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    config = importlib.import_module("config")
    roles = importlib.import_module("store.roles")
    dispatcher_roles = importlib.import_module("dispatcher_roles")

    assert config.PINNED_ADMIN_IDS == ()
    assert config.DISPATCHER_ADMIN_IDS == []
    assert config.DISPATCHER_USERS == {}
    assert roles.pinned_admin_ids() == frozenset()
    assert roles._bootstrap_admin_ids() == []
    assert dispatcher_roles._seed_admin_ids() == []


def test_clean_install_has_no_historic_chat_destinations(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    chats = importlib.import_module("telegram_chats")
    assert chats.TELEGRAM_CHATS_PROD == {}
    assert chats.TELEGRAM_CHATS_TEST == {}
    assert chats.TELEGRAM_LOGISTICS_CHATS_PROD == {}
    assert chats.TELEGRAM_LOGISTICS_CHATS_TEST == {}
