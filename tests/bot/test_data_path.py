"""The bot stores its state in Robopark's snapshotted data volume."""

from __future__ import annotations

import importlib
from pathlib import Path


APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_data_directory_can_be_mounted_inside_robopark_volume(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(APP))
    monkeypatch.setenv("ROBOPARK_BOT_DATA_DIR", str(tmp_path / "telegram-bot"))
    paths = importlib.import_module("paths")
    paths = importlib.reload(paths)
    assert paths.DATA_DIR == tmp_path / "telegram-bot"
    assert paths.LOGS_DIR == tmp_path / "telegram-bot" / "logs"
    monkeypatch.delenv("ROBOPARK_BOT_DATA_DIR")
    importlib.reload(paths)
