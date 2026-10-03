"""Custom Telegram roles must survive the bot's own user writer."""

from __future__ import annotations

import importlib
from pathlib import Path


APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_user_normalization_keeps_custom_role_and_scope(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    roles = importlib.import_module("dispatcher_roles")

    profile = roles.normalize_user_profile({
        "role": "senior_mechanic",
        "access": "location",
        "allowed_tags": ["Next"],
    })

    assert profile["role"] == "senior_mechanic"
    assert profile["access"] == "location"
    assert profile["allowed_tags"] == ["Next"]
