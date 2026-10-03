"""Admin UI: scheduled custom text broadcasts (including seeded builtins)."""

from __future__ import annotations

import threading
from html import escape
from typing import Any, Callable

from admin.nav import back_home_row
from admin.sessions import clear_session, get_session, set_session
from store.broadcasts import (
    REPEAT_DAILY,
    REPEAT_LABELS,
    REPEAT_ONCE,
    REPEAT_WEEKDAYS,
    add_campaign,
    campaign_by_id,
    delete_campaign,
    disable_all_campaigns,
    list_campaigns,
    removed_builtin_ids,
    restore_removed_builtins,
    send_campaign,
    set_campaign_enabled,
)
from store.roles import is_full_admin

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]


def _cid(parts: list[str], start: int = 3) -> str:
    return ":".join(parts[start:]) if len(parts) > start else ""


def _preview(text: str, limit: int = 120) -> str:
    t = text.replace("\n", " ")
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _row_title(row: dict[str, Any]) -> str:
    label = str(row.get("label") or "").strip()
    if label:
        return label
    return _preview(str(row.get("text") or ""), 32)


def broadcast_menu_text() -> str:
    rows = list_campaigns()
    active = [r for r in rows if r.get("enabled")]
    lines = [
        "<b>📢 Текстовая рассылка</b>",
        "Вшитые (АКБ/рессоры, конец дня, лидар) и ваши кампании.",
        "Вшитые: все чаты локаций (лидар — Москва). Свои: только <code>hourly_png</code>.",
        f"Активных: <b>{len(active)}</b> · всего: {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("Нет кампаний. Создайте новую или верните вшитые.")
    else:
        lines.append("<b>Кампании:</b>")
        for row in rows:
            flag = "✅" if row.get("enabled") else "⏸"
            kind = "вшитая" if row.get("builtin") else "своя"
            rep = REPEAT_LABELS.get(row.get("repeat") or REPEAT_ONCE, "?")
            title = escape(_row_title(row))
            lines.append(
                f"{flag} <code>{escape(str(row.get('fire_at') or '?'))}</code> "
                f"({rep}, {kind}) — {title}"
            )
    if removed_builtin_ids():
        lines.append("")
        lines.append("Часть вшитых удалена — можно вернуть кнопкой ниже.")
    return "\n".join(lines)


def broadcast_menu_keyboard() -> dict:
    rows: list[list[dict[str, str]]] = [
        [{"text": "➕ Новая рассылка", "callback_data": "adm:bcast:new"}],
    ]
    for row in list_campaigns():
        cid = str(row.get("id") or "")
        if not cid:
            continue
        flag = "✅" if row.get("enabled") else "⏸"
        pin = "📌 " if row.get("builtin") else ""
        label = f"{flag} {pin}{_preview(_row_title(row), 26)}"
        rows.append([{"text": label, "callback_data": f"adm:bcast:item:{cid}"}])
    if any(r.get("enabled") for r in list_campaigns()):
        rows.append([{"text": "⏹ Отключить все", "callback_data": "adm:bcast:offall"}])
    if removed_builtin_ids():
        rows.append([{"text": "↩️ Вернуть вшитые", "callback_data": "adm:bcast:restore"}])
    rows.append([{"text": "« Рассылки", "callback_data": "adm:menu:send"}])
    rows.append(back_home_row())
    return {"inline_keyboard": rows}


def _campaign_detail_text(cid: str) -> str:
    row = campaign_by_id(cid)
    if not row:
        return "Рассылка не найдена (удалена?)."
    kind = "вшитая" if row.get("builtin") else "своя"
    status = "включена" if row.get("enabled") else "выключена"
    rep = REPEAT_LABELS.get(row.get("repeat") or REPEAT_ONCE, "?")
    body = str(row.get("text") or "")
    if len(body) > 2800:
        body = body[:2799] + "…"
    return (
        f"<b>{escape(_row_title(row))}</b>\n"
        f"тип: {kind} · статус: <b>{status}</b>\n"
        f"время: <code>{escape(str(row.get('fire_at') or '?'))}</code> MSK ({rep})\n"
        f"id: <code>{escape(cid)}</code>\n\n"
        f"{escape(body)}"
    )


def _campaign_detail_keyboard(cid: str) -> dict:
    row = campaign_by_id(cid) or {}
    toggle = "⏸ Выключить" if row.get("enabled") else "▶️ Включить"
    return {
        "inline_keyboard": [
            [{"text": toggle, "callback_data": f"adm:bcast:toggle:{cid}"}],
            [{"text": "📤 Отправить сейчас", "callback_data": f"adm:bcast:send:{cid}"}],
            [{"text": "🗑 Удалить", "callback_data": f"adm:bcast:delask:{cid}"}],
            [{"text": "« К списку", "callback_data": "adm:bcast:menu"}],
        ]
    }


def _confirm_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "CONFIRM", "callback_data": "adm:bcast:confirm"}],
            [{"text": "Отмена", "callback_data": "adm:bcast:menu"}],
        ]
    }


def handle_broadcast_callback(
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

    action = parts[2] if len(parts) > 2 else "menu"

    if action == "menu":
        answer(callback_id)
        clear_session(user_id)
        send(
            user_id,
            broadcast_menu_text(),
            parse_mode="HTML",
            reply_markup=broadcast_menu_keyboard(),
        )
        return True

    if action == "item":
        cid = _cid(parts)
        answer(callback_id)
        send(
            user_id,
            _campaign_detail_text(cid),
            parse_mode="HTML",
            reply_markup=_campaign_detail_keyboard(cid),
        )
        return True

    if action == "toggle":
        cid = _cid(parts)
        row = campaign_by_id(cid)
        if not row:
            answer(callback_id, "нет")
            send(user_id, "Рассылка не найдена.")
            return True
        ok = set_campaign_enabled(cid, not bool(row.get("enabled")))
        answer(callback_id, "ok" if ok else "err")
        send(
            user_id,
            _campaign_detail_text(cid),
            parse_mode="HTML",
            reply_markup=_campaign_detail_keyboard(cid),
        )
        return True

    if action == "delask":
        cid = _cid(parts)
        row = campaign_by_id(cid)
        answer(callback_id)
        if not row:
            send(user_id, "Рассылка не найдена.")
            return True
        note = (
            "Вшитую можно вернуть кнопкой «Вернуть вшитые» в списке."
            if row.get("builtin")
            else "Своя кампания пропадёт без восстановления."
        )
        send(
            user_id,
            f"Удалить <b>{escape(_row_title(row))}</b>?\n{note}",
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "🗑 Удалить", "callback_data": f"adm:bcast:del:{cid}"}],
                    [{"text": "Отмена", "callback_data": f"adm:bcast:item:{cid}"}],
                ]
            },
        )
        return True

    if action == "del":
        cid = _cid(parts)
        ok = delete_campaign(cid)
        answer(callback_id, "ok" if ok else "err")
        send(
            user_id,
            ("🗑 Удалено.\n\n" if ok else "Не удалось удалить.\n\n") + broadcast_menu_text(),
            parse_mode="HTML",
            reply_markup=broadcast_menu_keyboard(),
        )
        return True

    if action == "restore":
        n = restore_removed_builtins()
        answer(callback_id, f"restore {n}")
        send(
            user_id,
            f"↩️ Возвращено вшитых: <b>{n}</b>\n\n" + broadcast_menu_text(),
            parse_mode="HTML",
            reply_markup=broadcast_menu_keyboard(),
        )
        return True

    if action == "new":
        answer(callback_id)
        set_session(user_id, "bcast_text")
        send(
            user_id,
            "Пришлите <b>текст рассылки</b> одним сообщением.\n"
            "Будет отправлен во все сервисные чаты локаций с hourly_png.\n"
            "/cancel — отмена.",
            parse_mode="HTML",
        )
        return True

    if action == "repeat" and len(parts) > 3:
        repeat = parts[3]
        if repeat not in (REPEAT_ONCE, REPEAT_DAILY, REPEAT_WEEKDAYS):
            answer(callback_id, "err")
            return True
        sess = get_session(user_id)
        if not sess or sess.get("kind") != "bcast_repeat":
            answer(callback_id, "сессия")
            send(user_id, "Начните с «Новая рассылка».")
            return True
        set_session(user_id, "bcast_time", text=sess.get("text"), repeat=repeat)
        answer(callback_id)
        send(
            user_id,
            f"Повтор: <b>{REPEAT_LABELS[repeat]}</b>\n"
            "Укажите время отправки в формате <code>HH:MM</code> (MSK).\n"
            "/cancel — отмена.",
            parse_mode="HTML",
        )
        return True

    if action == "confirm":
        answer(callback_id)
        sess = get_session(user_id)
        if not sess or sess.get("kind") != "bcast_confirm":
            send(user_id, "Нет черновика. Начните заново.")
            return True
        try:
            row = add_campaign(
                text=str(sess.get("text") or ""),
                fire_at=str(sess.get("fire_at") or ""),
                repeat=str(sess.get("repeat") or REPEAT_ONCE),
                created_by=user_id,
            )
        except (ValueError, KeyError) as e:
            send(user_id, f"Ошибка: {e}")
            return True
        clear_session(user_id)
        rep = REPEAT_LABELS.get(row.get("repeat") or REPEAT_ONCE, "?")
        send(
            user_id,
            "✅ Рассылка создана и <b>включена</b>\n"
            f"id: <code>{row['id']}</code>\n"
            f"время: <code>{row['fire_at']}</code> ({rep})\n"
            f"текст: {_preview(str(row['text']), 200)}",
            parse_mode="HTML",
            reply_markup=broadcast_menu_keyboard(),
        )
        return True

    if action == "off":
        cid = _cid(parts)
        ok = set_campaign_enabled(cid, False)
        answer(callback_id, "ok" if ok else "err")
        send(
            user_id,
            broadcast_menu_text(),
            parse_mode="HTML",
            reply_markup=broadcast_menu_keyboard(),
        )
        return True

    if action == "offall":
        n = disable_all_campaigns()
        answer(callback_id, f"off {n}")
        send(
            user_id,
            f"⏹ Отключено рассылок: <b>{n}</b>\n\n" + broadcast_menu_text(),
            parse_mode="HTML",
            reply_markup=broadcast_menu_keyboard(),
        )
        return True

    if action == "send":
        cid = _cid(parts)
        answer(callback_id, "отправка…")
        threading.Thread(
            target=_send_now_worker,
            args=(cid, user_id, send),
            daemon=True,
            name=f"bcast-send-{cid}",
        ).start()
        return True

    answer(callback_id)
    return True


def _send_now_worker(campaign_id: str, user_id: int, send: SendFn) -> None:
    try:
        sent, skipped = send_campaign(campaign_id, mark_state=False)
        send(
            user_id,
            f"📤 Тестовая отправка <code>{escape(campaign_id)}</code>\n"
            f"доставлено: {sent}, пропущено: {skipped}",
            parse_mode="HTML",
        )
    except Exception as e:
        send(user_id, f"❌ Ошибка: {e}")


def handle_broadcast_text(*, user_id: int, text: str, send: SendFn) -> bool:
    sess = get_session(user_id)
    if not sess:
        return False
    kind = sess.get("kind")

    if kind == "bcast_text":
        body = text.strip()
        if not body:
            send(user_id, "Пустой текст")
            return True
        if len(body) > 4000:
            send(user_id, "Слишком длинный текст (макс. 4000)")
            return True
        set_session(user_id, "bcast_repeat", text=body)
        send(
            user_id,
            f"Текст принят ({len(body)} симв.).\n<b>Нужны повторения?</b>",
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Один раз", "callback_data": "adm:bcast:repeat:once"}],
                    [{"text": "Каждый день", "callback_data": "adm:bcast:repeat:daily"}],
                    [{"text": "Пн–Пт", "callback_data": "adm:bcast:repeat:weekdays"}],
                    [{"text": "Отмена", "callback_data": "adm:bcast:menu"}],
                ]
            },
        )
        return True

    if kind == "bcast_time":
        try:
            from store.schedules import normalize_fire_at

            fire_at = normalize_fire_at(text.strip())
        except ValueError as e:
            send(user_id, f"Ошибка времени: {e}")
            return True
        repeat = str(sess.get("repeat") or REPEAT_ONCE)
        body = str(sess.get("text") or "")
        set_session(user_id, "bcast_confirm", text=body, repeat=repeat, fire_at=fire_at)
        rep = REPEAT_LABELS.get(repeat, repeat)
        send(
            user_id,
            "<b>Проверьте рассылку</b>\n"
            f"время: <code>{fire_at}</code> MSK\n"
            f"повтор: {rep}\n\n"
            f"{escape(body)}\n\n"
            "Подтвердите создание:",
            parse_mode="HTML",
            reply_markup=_confirm_keyboard(),
        )
        return True

    return False
