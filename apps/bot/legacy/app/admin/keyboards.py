"""Reply keyboards: users vs full-admins."""

from __future__ import annotations

BTN_HELP = "ℹ️ Помощь"
BTN_ROBOT = "🔍 Робот"
BTN_ADMIN = "⚙️ Управление"
BTN_STATUS = "📊 Статус"
BTN_PAUSE = "⏸ Пауза"
BTN_RESUME = "▶️ Старт"

USER_REPLY_BUTTONS = frozenset({BTN_HELP, BTN_ROBOT})
ADMIN_REPLY_BUTTONS = frozenset({BTN_ADMIN, BTN_STATUS, BTN_PAUSE, BTN_RESUME})


def user_reply_keyboard() -> dict:
    return {
        "keyboard": [[{"text": BTN_HELP}, {"text": BTN_ROBOT}]],
        "resize_keyboard": True,
    }


def full_admin_reply_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": BTN_HELP}, {"text": BTN_ROBOT}],
            [{"text": BTN_ADMIN}, {"text": BTN_STATUS}],
            [{"text": BTN_PAUSE}, {"text": BTN_RESUME}],
        ],
        "resize_keyboard": True,
    }


def hide_reply_keyboard() -> dict:
    """Telegram ReplyKeyboardRemove — strips a leftover bottom bar."""
    return {"remove_keyboard": True}


def shows_bottom_reply_keyboard(markup: dict | None) -> bool:
    return bool(markup and markup.get("keyboard"))


def reply_keyboard_for(user_id: int) -> dict | None:
    try:
        from dispatcher_roles import has_global_robot_search, is_admin_user

        if is_admin_user(user_id):
            return full_admin_reply_keyboard()
        from dispatcher_auth import get_user_profile

        profile = get_user_profile(user_id)
        if not profile:
            return None
        if has_global_robot_search(user_id, profile):
            return user_reply_keyboard()
        return hide_reply_keyboard()
    except Exception:
        return None
