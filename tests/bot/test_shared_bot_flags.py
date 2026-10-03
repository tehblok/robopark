"""Telegram and owner controls share private atomic runtime flags."""

from __future__ import annotations

import importlib
import stat
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_telegram_pause_and_profile_flags_are_private(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(APP))
    runtime = importlib.import_module("store.runtime")
    chats = importlib.import_module("telegram_chats")
    pause = tmp_path / ".send_paused"
    profile = tmp_path / ".telegram_profile"
    monkeypatch.setattr(runtime, "SEND_PAUSE", pause)
    monkeypatch.setattr(chats, "PROFILE_FILE", profile)

    runtime.set_send_paused(True)
    chats.set_profile("test")

    assert pause.read_bytes() == b"1\n"
    assert profile.read_bytes() == b"test\n"
    assert stat.S_IMODE(pause.stat().st_mode) == 0o600
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600
