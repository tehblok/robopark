"""Admin location list/edit callbacks."""

from __future__ import annotations

from typing import Any, Callable

from admin.nav import back_home_row, pair_row, rows_with_nav
from admin.sessions import set_session
from store.locations import load_locations, update_location
from store.roles import is_full_admin

_FLAG_LABELS = {
    "hourly_png": "PNG-отчёт",
    "killswitch": "Killswitch",
    "sdcwh_zip": "SDCWH ZIP",
    "robomaint_moves": "ROBOMAINT",
}

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]


def _flag_btn(slug: str, flag: str, enabled: bool) -> dict[str, str]:
    label = _FLAG_LABELS.get(flag, flag)
    mark = "✅" if enabled else "⬜"
    return {
        "text": f"{mark} {label}",
        "callback_data": f"adm:loc:tog:{slug}:{flag}",
    }


def _view_keyboard(slug: str, loc: dict) -> dict:
    part = loc["participation"]
    return rows_with_nav(
        [
            pair_row(
                _flag_btn(slug, "hourly_png", part.get("hourly_png")),
                _flag_btn(slug, "killswitch", part.get("killswitch")),
            ),
            pair_row(
                _flag_btn(slug, "sdcwh_zip", part.get("sdcwh_zip")),
                _flag_btn(slug, "robomaint_moves", part.get("robomaint_moves")),
            ),
            [{"text": "💬 Чат (текущий профиль)", "callback_data": f"adm:loc:editchat:{slug}"}],
            [{"text": "✏️ Имя", "callback_data": f"adm:loc:editname:{slug}"}],
            [{"text": "🏷 Tracker tag", "callback_data": f"adm:loc:edittag:{slug}"}],
            [{"text": "« К списку", "callback_data": "adm:loc:list"}],
        ],
        back_home_row(),
    )


def handle_loc_callback(
    *,
    user_id: int,
    parts: list[str],
    callback_id: str,
    send: SendFn,
    answer: AnswerFn,
) -> bool:
    if not is_full_admin(user_id):
        answer(callback_id, "Нет прав")
        return True
    action = parts[2] if len(parts) > 2 else "list"

    if action == "list":
        answer(callback_id)
        rows = []
        for loc in load_locations():
            rows.append(
                [
                    {
                        "text": f"{loc['display_name']} ({loc['tracker_tag']})",
                        "callback_data": f"adm:loc:view:{loc['slug']}",
                    }
                ]
            )
        rows.append(back_home_row())
        send(user_id, "<b>📍 Локации</b>", parse_mode="HTML", reply_markup={"inline_keyboard": rows})
        return True

    if action == "view" and len(parts) > 3:
        slug = parts[3]
        loc = next((x for x in load_locations() if x["slug"] == slug), None)
        answer(callback_id)
        if not loc:
            send(user_id, "Локация не найдена")
            return True
        prod = loc["chats"].get("prod") or {}
        test = loc["chats"].get("test") or {}
        part = loc["participation"]
        text = (
            f"<b>{loc['display_name']}</b>\n"
            f"key: <code>{loc['key']}</code>\n"
            f"tag: <code>{loc['tracker_tag']}</code>\n"
            f"prod: <code>{prod.get('chat_id')}/{prod.get('thread_id')}</code>\n"
            f"test: <code>{test.get('chat_id')}/{test.get('thread_id')}</code>\n"
            f"hourly={part.get('hourly_png')} killswitch={part.get('killswitch')}\n"
            f"sdcwh={part.get('sdcwh_zip')} robomaint={part.get('robomaint_moves')}"
        )
        send(user_id, text, parse_mode="HTML", reply_markup=_view_keyboard(slug, loc))
        return True

    if action in ("editchat", "editname", "edittag") and len(parts) > 3:
        slug = parts[3]
        loc = next((x for x in load_locations() if x["slug"] == slug), None)
        answer(callback_id)
        if not loc:
            send(user_id, "Не найдено")
            return True
        if action == "editchat":
            set_session(user_id, "loc_edit_chat", location_key=loc["key"])
            send(
                user_id,
                f"Локация <b>{loc['display_name']}</b> — чат для <b>текущего</b> профиля.\n"
                "Пришлите: <code>CHAT_ID THREAD_ID</code> или <code>CHAT_ID none</code>.\n"
                "/cancel — отмена.",
                parse_mode="HTML",
            )
        elif action == "editname":
            set_session(user_id, "loc_edit_name", location_key=loc["key"])
            send(
                user_id,
                f"Текущее имя: <b>{loc['display_name']}</b>\nПришлите новое display name.\n/cancel",
                parse_mode="HTML",
            )
        else:
            set_session(user_id, "loc_edit_tag", location_key=loc["key"])
            send(
                user_id,
                f"Текущий tag: <code>{loc['tracker_tag']}</code>\n"
                "Пришлите новый Tracker tag (без слияния ключей).\n/cancel",
                parse_mode="HTML",
            )
        return True

    if action == "tog" and len(parts) > 4:
        slug = parts[3]
        flag = parts[4]
        loc = next((x for x in load_locations() if x["slug"] == slug), None)
        if not loc or flag not in loc["participation"]:
            answer(callback_id, "err")
            return True
        new_val = not bool(loc["participation"][flag])
        update_location(loc["key"], participation={flag: new_val})
        answer(callback_id, f"{flag}={new_val}")
        return handle_loc_callback(
            user_id=user_id,
            parts=["adm", "loc", "view", slug],
            callback_id=callback_id,
            send=send,
            answer=lambda *a, **k: None,
        )

    answer(callback_id)
    return True
