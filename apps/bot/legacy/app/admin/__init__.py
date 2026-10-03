"""Telegram admin surface (full admins only)."""

from __future__ import annotations

from admin.menu import admin_home_keyboard, admin_home_text, handle_admin_callback, handle_admin_text


def handle_admin_document(*, user_id, chat, document, send, download_file):
    """Reject old Telegram OTA uploads before fetching any file bytes."""
    del download_file
    if chat.get("type") != "private":
        return False
    if str(document.get("file_name") or "").lower().endswith(".zip"):
        send(user_id, "Архивы обновлений загружаются через Robopark.")
        return True
    return False

__all__ = [
    "admin_home_keyboard",
    "admin_home_text",
    "handle_admin_callback",
    "handle_admin_text",
    "handle_admin_document",
]
