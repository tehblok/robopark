"""Ежедневное приветствие диспетчер-бота: один раз в день на пользователя."""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime
from pathlib import Path

from config import DATA_DIR, DISPATCHER_DEFAULT_GREETING

_ROBOT_HINT = re.compile(r"номер робота|кидай|отправ", re.IGNORECASE)

STATE_FILE = Path(DATA_DIR) / "dispatcher_daily_greetings.json"
_lock = threading.Lock()


def _load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_state(state: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def needs_daily_greeting(user_id: int) -> bool:
    today = date.today().isoformat()
    with _lock:
        state = _load_state()
        return state.get(str(user_id)) != today


def mark_greeted(user_id: int) -> None:
    today = date.today().isoformat()
    with _lock:
        state = _load_state()
        state[str(user_id)] = today
        _save_state(state)


def _time_opener(name: str) -> str:
    hour = datetime.now().hour
    if name:
        if 5 <= hour < 12:
            return f"Доброе утро, {name}!"
        if 12 <= hour < 18:
            return f"Привет, {name}!"
        if 18 <= hour < 23:
            return f"Добрый вечер, {name}!"
        return f"Привет, {name}!"
    if 5 <= hour < 12:
        return "Доброе утро!"
    if 18 <= hour < 23:
        return "Добрый вечер!"
    return "Привет!"


def daily_opener(profile: dict, *, for_robot_query: bool = False) -> str:
    """Текст первого приветствия за день (без отправки в Telegram)."""
    name = profile.get("name") or ""
    custom = profile.get("greeting")

    if for_robot_query:
        if custom and not _ROBOT_HINT.search(custom):
            return custom
        return _time_opener(name)

    if custom:
        return custom
    return DISPATCHER_DEFAULT_GREETING.format(name=name)


def prepend_daily_opener(
    user_id: int,
    profile: dict,
    body: str,
    *,
    for_robot_query: bool = False,
) -> tuple[str, bool, str | None]:
    """Добавить приветствие к телу сообщения, если сегодня ещё не здоровались."""
    if not needs_daily_greeting(user_id):
        return body, False, None
    opener = daily_opener(profile, for_robot_query=for_robot_query)
    return f"{opener}\n\n{body}", True, opener
