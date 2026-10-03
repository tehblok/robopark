#!/usr/bin/env python3
"""Переключение prod / test для Telegram-рассылок.

  python switch_telegram.py prod    # prod-чаты (удаляет .telegram_profile)
  python switch_telegram.py test    # тест-чат
  python switch_telegram.py status  # текущий профиль и незаполненные test thread_id
"""

from __future__ import annotations

import sys

from telegram_chats import (
    get_chats,
    missing_test_threads,
    resolve_profile,
    set_profile,
)


def show_status() -> None:
    profile = resolve_profile()
    chats, _ = get_chats(profile)
    print(f"Профиль: {profile}")
    print(f"Локаций в TELEGRAM_CHATS: {len(chats)}")
    for loc, chat in chats.items():
        if chat is None:
            print(f"  {loc}: не настроено")
        else:
            print(f"  {loc}: chat_id={chat[0]}, thread_id={chat[1]}")
    missing = missing_test_threads()
    if missing:
        print(f"\n⚠️  В test-профиле без thread_id: {', '.join(missing)}")
        print("   Создайте темы в тест-чате и обновите telegram_chats.py (bot_listener.py → «ID»).")
    else:
        print("\n✅ Все test thread_id заполнены.")


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1].strip().lower()
    if cmd == "status":
        show_status()
        return

    if cmd not in ("prod", "test"):
        print(f"❌ Неизвестная команда: {cmd}")
        sys.exit(1)

    if cmd == "test":
        missing = missing_test_threads()
        if missing:
            print(f"⚠️  test-профиль неполный (нет thread_id): {', '.join(missing)}")
            print("   Рассылка пропустит эти локации. Заполните telegram_chats.py перед полным прогоном.")

    set_profile(cmd)
    chats, _ = get_chats(cmd)
    n = sum(1 for c in chats.values() if c and c[1] is not None)
    print(f"✅ Профиль → {cmd} ({n}/{len(chats)} локаций с thread_id)")
    if cmd == "prod":
        print("   Cron и ручной запуск без TELEGRAM_PROFILE используют prod-чаты.")
    else:
        print("   Прогон: venv/bin/python telegram_sender.py")
        print("   Вернуть prod: python switch_telegram.py prod")


if __name__ == "__main__":
    main()
