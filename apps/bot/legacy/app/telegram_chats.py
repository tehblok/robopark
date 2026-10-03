"""Prod и test Telegram-чаты для рассылок.

Переключение:
  python switch_telegram.py test   # записать профиль в .telegram_profile
  python switch_telegram.py prod   # вернуть prod (cron по умолчанию)
  python switch_telegram.py status

Разовый прогон без смены файла:
  TELEGRAM_PROFILE=test venv/bin/python telegram_sender.py

Приоритет профиля: переменная TELEGRAM_PROFILE → .telegram_profile → prod.
Cron не задаёт профиль — всегда prod, если локально не переключено на test.
"""

from __future__ import annotations

import os

from paths import DATA_DIR
from store.shared_flags import write_optional_flag

PROFILE_FILE = DATA_DIR / ".telegram_profile"

# Destinations are imported from the operator's data archive, never shipped
# as implicit recipients in a clean installation.
TELEGRAM_CHATS_PROD: dict[str, tuple[int, int] | None] = {}
TELEGRAM_CHATS_TEST: dict[str, tuple[int, int] | None] = {}
TELEGRAM_LOGISTICS_CHATS_PROD: dict[str, tuple[int, int]] = {}
TELEGRAM_LOGISTICS_CHATS_TEST: dict[str, tuple[int, int]] = {}

PROFILES = ("prod", "test")


def resolve_profile() -> str:
    env = os.environ.get("TELEGRAM_PROFILE", "").strip().lower()
    if env in PROFILES:
        return env
    if PROFILE_FILE.exists():
        saved = PROFILE_FILE.read_text(encoding="utf-8").strip().lower()
        if saved in PROFILES:
            return saved
    return "prod"


def set_profile(name: str) -> str:
    profile = name.strip().lower()
    if profile not in PROFILES:
        raise ValueError(f"Профиль должен быть один из: {', '.join(PROFILES)}")
    write_optional_flag(PROFILE_FILE, None if profile == "prod" else b"test\n")
    return profile


def get_chats(profile: str | None = None) -> tuple[dict, dict]:
    p = profile or resolve_profile()
    if p == "test":
        return TELEGRAM_CHATS_TEST, TELEGRAM_LOGISTICS_CHATS_TEST
    return TELEGRAM_CHATS_PROD, TELEGRAM_LOGISTICS_CHATS_PROD


def missing_test_threads() -> list[str]:
    return [
        loc
        for loc, chat in TELEGRAM_CHATS_TEST.items()
        if chat is None or chat[1] is None
    ]


def missing_prod_chats() -> list[str]:
    return [loc for loc, chat in TELEGRAM_CHATS_PROD.items() if chat is None]
