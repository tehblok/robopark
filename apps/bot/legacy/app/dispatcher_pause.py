"""Пауза диспетчер-бота для пользователей (админы работают как обычно)."""

from __future__ import annotations

import threading
from pathlib import Path

from config import DATA_DIR

PAUSE_FILE = Path(DATA_DIR) / ".dispatcher_paused"
_lock = threading.Lock()

ADMIN_COMMANDS_TEXT = (
    "Команды администратора:\n"
    "• кнопки: ℹ️ Помощь · 🔍 Робот\n"
    "• ⚙️ Управление · 📊 Статус · ⏸ Пауза · ▶️ Старт\n"
    "• /admin · /stats · /users · /pause · /resume\n"
    "\n"
    "Одобрение заявок — кнопки в сообщениях о новых пользователях."
)

USER_PAUSED_MSG = (
    "Бот временно недоступен. Попробуйте позже или обратитесь в сервисный чат."
)


def is_paused() -> bool:
    return PAUSE_FILE.exists()


def set_paused(paused: bool) -> None:
    with _lock:
        PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if paused:
            PAUSE_FILE.touch(exist_ok=True)
        elif PAUSE_FILE.exists():
            PAUSE_FILE.unlink()


def pause_status_line() -> str:
    return "⏸ Пауза: запросы робота для пользователей отключены" if is_paused() else "▶️ Бот активен для пользователей"


def admin_help_text() -> str:
    return f"{pause_status_line()}\n\n{ADMIN_COMMANDS_TEXT}"
