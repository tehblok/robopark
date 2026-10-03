"""Admin home menu and callback router (prefix adm:)."""

from __future__ import annotations

from typing import Any, Callable

from admin.nav import back_home_row, cached_health_snapshot, pair_row, rows_with_nav, status_line
from store.roles import is_full_admin
from telegram_chats import resolve_profile, set_profile

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]


def admin_home_text(*, compact: bool = False) -> str:
    h = cached_health_snapshot()
    if compact:
        return f"<b>Управление</b>\n{status_line()}"
    return (
        f"<b>Управление</b>\n"
        f"{status_line()}\n"
        f"PID: <code>{h.get('pid') or '—'}</code>\n\n"
        "Выберите раздел:"
    )


def admin_home_keyboard() -> dict:
    return {
        "inline_keyboard": [
            pair_row(
                {"text": "📍 Локации", "callback_data": "adm:loc:list"},
                {"text": "👥 Люди", "callback_data": "adm:users"},
            ),
            [{"text": "📢 Рассылки и отчёты", "callback_data": "adm:menu:send"}],
            [{"text": "⚙️ Система", "callback_data": "adm:menu:sys"}],
            [{"text": "🛠 Хост и обновления", "callback_data": "adm:menu:host"}],
        ]
    }


def _send_menu_keyboard() -> dict:
    return rows_with_nav(
        [
            pair_row(
                {"text": "📤 PNG сейчас", "callback_data": "adm:sys:send_ask"},
                {"text": "📢 Текст", "callback_data": "adm:bcast:menu"},
            ),
            pair_row(
                {"text": "🍩 СК", "callback_data": "adm:sk:menu"},
                {"text": "🕐 Расписание", "callback_data": "adm:sched:list"},
            ),
        ],
        back_home_row(),
    )


def _sys_menu_keyboard() -> dict:
    return rows_with_nav(
        [
            [{"text": "⏯ Паузы и статус", "callback_data": "adm:sys:status"}],
            [{"text": "🔀 Профиль prod/test", "callback_data": "adm:profile"}],
        ],
        back_home_row(),
    )


def host_menu_text() -> str:
    return "<b>🛠 Хост и обновления</b>\n" + status_line()


def host_menu_keyboard() -> dict:
    return _host_menu_keyboard()


def _host_menu_keyboard() -> dict:
    return rows_with_nav(
        [
            [{"text": "🔑 Настройка токенов", "callback_data": "adm:sys:tokens"}],
        ],
        back_home_row(),
    )


def quick_status_text() -> str:
    from store.schedules import send_window_label
    from store.broadcasts import list_campaigns

    h = cached_health_snapshot()
    active_bcast = sum(1 for r in list_campaigns() if r.get("enabled"))
    return (
        f"<b>Быстрый статус</b>\n"
        f"{status_line()}\n"
        f"PNG: {send_window_label()}\n"
        f"Текст. рассылок активно: {active_bcast}\n"
        f"PID: <code>{h.get('pid') or '—'}</code>\n"
        f"RSS: {h.get('rss_mb', 0)} МБ"
    )


def handle_admin_callback(
    *,
    user_id: int,
    data: str,
    callback_id: str,
    send: SendFn,
    answer: AnswerFn,
    send_document: Callable[..., Any] | None = None,
    send_new: SendFn | None = None,
) -> bool:
    """Return True if handled."""
    if not data.startswith("adm:"):
        return False
    if not is_full_admin(user_id):
        answer(callback_id, "Нет прав")
        return True

    parts = data.split(":")
    section = parts[1] if len(parts) > 1 else ""

    if section in {"ota", "export", "heal", "hk"}:
        answer(callback_id)
        send(user_id, "Управление хостом и обновлениями выполняется в Robopark.")
        return True

    if section == "home":
        answer(callback_id)
        send(user_id, admin_home_text(), reply_markup=admin_home_keyboard(), parse_mode="HTML")
        return True

    if section == "menu" and len(parts) > 2:
        submenu = parts[2]
        answer(callback_id)
        if submenu == "send":
            send(
                user_id,
                "<b>📢 Рассылки и отчёты</b>\n" + status_line(),
                parse_mode="HTML",
                reply_markup=_send_menu_keyboard(),
            )
            return True
        if submenu == "sys":
            send(
                user_id,
                "<b>⚙️ Система</b>\n" + status_line(),
                parse_mode="HTML",
                reply_markup=_sys_menu_keyboard(),
            )
            return True
        if submenu == "host":
            send(
                user_id,
                host_menu_text(),
                parse_mode="HTML",
                reply_markup=host_menu_keyboard(),
            )
            return True

    if section == "loc":
        from admin.locations_ui import handle_loc_callback

        return handle_loc_callback(
            user_id=user_id, parts=parts, callback_id=callback_id, send=send, answer=answer
        )

    if section == "users":
        from admin.users_ui import handle_users_callback

        return handle_users_callback(
            user_id=user_id, parts=parts, callback_id=callback_id, send=send, answer=answer
        )

    if section == "sys":
        from admin.system import handle_sys_callback

        return handle_sys_callback(
            user_id=user_id, parts=parts, callback_id=callback_id, send=send, answer=answer
        )

    if section == "ota":
        from admin.ota import handle_ota_callback

        return handle_ota_callback(
            user_id=user_id, parts=parts, callback_id=callback_id, send=send, answer=answer
        )

    if section == "export":
        from admin.export import handle_export_callback

        return handle_export_callback(
            user_id=user_id,
            parts=parts,
            callback_id=callback_id,
            send=send,
            answer=answer,
            send_document=send_document,
            send_new=send_new,
        )

    if section == "sched":
        from admin.schedules_ui import handle_schedules_callback

        return handle_schedules_callback(
            user_id=user_id,
            parts=parts,
            callback_id=callback_id,
            send=send,
            answer=answer,
        )

    if section == "bcast":
        from admin.broadcast_ui import handle_broadcast_callback

        return handle_broadcast_callback(
            user_id=user_id,
            parts=parts,
            callback_id=callback_id,
            send=send,
            answer=answer,
        )

    if section == "sk":
        from admin.sk_ui import handle_sk_callback

        return handle_sk_callback(
            user_id=user_id,
            parts=parts,
            callback_id=callback_id,
            send=send,
            answer=answer,
        )

    if section == "heal":
        from admin.system import handle_heal_callback

        return handle_heal_callback(
            user_id=user_id, parts=parts, callback_id=callback_id, send=send, answer=answer
        )

    if section == "hk":
        from admin.nav import invalidate_health_cache
        from store.housekeep import format_disk_report, maybe_run

        action = parts[2] if len(parts) > 2 else "menu"
        kb = {
            "inline_keyboard": [
                [{"text": "🧹 Очистить сейчас", "callback_data": "adm:hk:run"}],
                [{"text": "« Хост", "callback_data": "adm:menu:host"}],
                back_home_row(),
            ]
        }
        if action == "run":
            result = maybe_run(force=True)
            invalidate_health_cache()
            answer(callback_id, "очистка")
            send(
                user_id,
                format_disk_report(result=result),
                parse_mode="HTML",
                reply_markup=kb,
            )
            return True
        answer(callback_id)
        send(user_id, format_disk_report(), parse_mode="HTML", reply_markup=kb)
        return True

    if section == "profile":
        if len(parts) >= 4 and parts[2] == "set":
            name = parts[3]
            set_profile(name)
            try:
                from config import refresh_telegram_chats

                refresh_telegram_chats()
            except Exception:
                pass
            from admin.nav import invalidate_health_cache

            invalidate_health_cache()
            answer(callback_id, f"profile={name}")
            send(
                user_id,
                f"Профиль: <b>{name}</b>",
                parse_mode="HTML",
                reply_markup=_sys_menu_keyboard(),
            )
            return True
        answer(callback_id)
        cur = resolve_profile()
        other = "test" if cur == "prod" else "prod"
        send(
            user_id,
            f"Текущий профиль: <b>{cur}</b>\nПереключить на {other}?",
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": f"→ {other}", "callback_data": f"adm:profile:set:{other}"}],
                    back_home_row(),
                ]
            },
        )
        return True

    answer(callback_id)
    return True


def handle_admin_text(*, user_id: int, text: str, send: SendFn) -> bool:
    """Handle multi-step admin text (token CONFIRM, location edits, roles)."""
    if not is_full_admin(user_id):
        return False
    from admin.sessions import clear_session, get_session
    from admin.system import handle_token_text
    from admin.users_ui import handle_users_text
    from admin.schedules_ui import handle_schedules_text
    from admin.broadcast_ui import handle_broadcast_text
    from admin.sk_ui import handle_sk_text
    from store.locations import update_location
    from telegram_chats import resolve_profile

    sess = get_session(user_id)
    if not sess:
        return False
    raw = text.strip()
    from admin.keyboards import ADMIN_REPLY_BUTTONS, USER_REPLY_BUTTONS

    if raw in USER_REPLY_BUTTONS | ADMIN_REPLY_BUTTONS:
        return False
    cmd = raw.split()[0].split("@")[0].lower() if raw.startswith("/") else ""
    if cmd == "/cancel":
        clear_session(user_id)
        send(user_id, "Отменено.", reply_markup=admin_home_keyboard(), parse_mode="HTML")
        return True
    # Never swallow /admin /start /help — otherwise a stuck wizard eats them.
    if cmd.startswith("/"):
        return False

    kind = sess.get("kind")

    if kind == "loc_edit_chat":
        parts = text.strip().split()
        if len(parts) != 2:
            send(user_id, "Формат: CHAT_ID THREAD_ID")
            return True
        try:
            chat_id = int(parts[0])
            thread_raw = parts[1].lower()
            thread_id = None if thread_raw in ("none", "null", "-") else int(thread_raw)
        except ValueError:
            send(user_id, "CHAT_ID и THREAD_ID должны быть числами (или none).")
            return True
        key = sess.get("location_key")
        profile = resolve_profile()
        update_location(key, chats={profile: {"chat_id": chat_id, "thread_id": thread_id}})
        clear_session(user_id)
        send(
            user_id,
            f"✅ Сохранено ({profile}): <code>{chat_id}/{thread_id}</code>",
            parse_mode="HTML",
            reply_markup=admin_home_keyboard(),
        )
        return True

    if kind == "loc_edit_name":
        name = text.strip()
        if not name:
            send(user_id, "Пустое имя")
            return True
        update_location(sess.get("location_key"), display_name=name)
        clear_session(user_id)
        send(user_id, f"✅ display_name → <b>{name}</b>", parse_mode="HTML")
        return True

    if kind == "loc_edit_tag":
        tag = text.strip()
        if not tag:
            send(user_id, "Пустой tag")
            return True
        update_location(sess.get("location_key"), tracker_tag=tag)
        clear_session(user_id)
        send(user_id, f"✅ tracker_tag → <code>{tag}</code>", parse_mode="HTML")
        return True

    if handle_users_text(user_id=user_id, text=text, send=send):
        return True
    if handle_schedules_text(user_id=user_id, text=text, send=send):
        return True
    if handle_broadcast_text(user_id=user_id, text=text, send=send):
        return True
    if handle_sk_text(user_id=user_id, text=text, send=send):
        return True
    return handle_token_text(user_id=user_id, text=text, send=send)
