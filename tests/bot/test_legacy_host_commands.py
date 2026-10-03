"""Old host commands cannot bypass Robopark's deployment controls."""

from __future__ import annotations

import importlib
from pathlib import Path


APP = Path(__file__).resolve().parents[2] / "apps/bot/legacy/app"


def test_old_ota_callback_is_redirected_to_robopark(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    menu = importlib.import_module("admin.menu")
    monkeypatch.setattr(menu, "is_full_admin", lambda _user_id: True)
    messages = []
    assert menu.handle_admin_callback(
        user_id=1,
        data="adm:ota:apply:incoming.zip",
        callback_id="test",
        send=lambda _user_id, message, **_kwargs: messages.append(message),
        answer=lambda *_args, **_kwargs: None,
    )
    assert messages and "Robopark" in messages[0]


def test_old_ota_document_is_never_downloaded(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    admin = importlib.import_module("admin")
    downloaded = []
    assert admin.handle_admin_document(
        user_id=1,
        chat={"type": "private"},
        document={"file_name": "update.zip", "file_id": "file"},
        send=lambda *_args, **_kwargs: None,
        download_file=lambda *_args: downloaded.append(True),
    )
    assert not downloaded
