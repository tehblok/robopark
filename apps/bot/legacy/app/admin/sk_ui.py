"""Admin UI: killswitch (СК) donut campaigns."""

from __future__ import annotations

import threading
from html import escape
from typing import Any, Callable

from admin.nav import back_home_row, pair_row
from admin.sessions import clear_session, get_session, set_session
from killswitch_logic import campaign_query
from store.broadcasts import REPEAT_DAILY, REPEAT_LABELS, REPEAT_ONCE, REPEAT_WEEKDAYS
from store.locations import load_locations, location_by_key
from store.roles import is_full_admin
from store.sk_campaigns import (
    add_campaign,
    campaign_by_id,
    delete_campaign,
    list_campaigns,
    send_campaign,
    set_campaign_enabled,
)

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]


def _cid(parts: list[str], start: int = 3) -> str:
    return ":".join(parts[start:]) if len(parts) > start else ""


def _park_label(key: str) -> str:
    loc = location_by_key(key)
    if not loc:
        return key
    return f"{loc['display_name']} ({loc['tracker_tag']})"


def sk_menu_text() -> str:
    rows = list_campaigns()
    active = [r for r in rows if r.get("enabled")]
    lines = [
        "<b>🍩 СК</b>",
        "Диаграмма прогресса по тегу Tracker. Парки — и тикеты, и чат.",
        f"Активных: <b>{len(active)}</b> · всего: {len(rows)}",
        "",
    ]
    if not rows:
        lines.append("Нет кампаний. Нажмите «Добавить СК».")
        return "\n".join(lines)
    lines.append("<b>Кампании:</b>")
    for row in rows:
        flag = "✅" if row.get("enabled") else "⏸"
        rep = REPEAT_LABELS.get(row.get("repeat") or REPEAT_ONCE, "?")
        tag = escape(str(row.get("tag") or "?"))
        n = len(row.get("location_keys") or [])
        lines.append(
            f"{flag} <code>{escape(str(row.get('fire_at') or '?'))}</code> "
            f"({rep}) — тег <code>{tag}</code> · парков: {n}"
        )
    return "\n".join(lines)


def sk_menu_keyboard() -> dict:
    rows: list[list[dict[str, str]]] = [
        [{"text": "➕ Добавить СК", "callback_data": "adm:sk:new"}],
    ]
    for row in list_campaigns():
        cid = str(row.get("id") or "")
        if not cid:
            continue
        flag = "✅" if row.get("enabled") else "⏸"
        tag = str(row.get("tag") or cid)
        label = f"{flag} {tag}"[:40]
        rows.append([{"text": label, "callback_data": f"adm:sk:item:{cid}"}])
    rows.append([{"text": "« Рассылки", "callback_data": "adm:menu:send"}])
    rows.append(back_home_row())
    return {"inline_keyboard": rows}


def _detail_text(cid: str) -> str:
    row = campaign_by_id(cid)
    if not row:
        return "СК не найдена (удалена?)."
    status = "включена" if row.get("enabled") else "выключена"
    rep = REPEAT_LABELS.get(row.get("repeat") or REPEAT_ONCE, "?")
    parks = row.get("location_keys") or []
    park_lines = "\n".join(f"• {escape(_park_label(k))}" for k in parks) or "—"
    return (
        f"<b>СК <code>{escape(str(row.get('tag')))}</code></b>\n"
        f"статус: <b>{status}</b>\n"
        f"время: <code>{escape(str(row.get('fire_at') or '?'))}</code> MSK ({rep})\n"
        f"id: <code>{escape(cid)}</code>\n\n"
        f"<b>Парки</b>\n{park_lines}"
    )


def _detail_keyboard(cid: str) -> dict:
    row = campaign_by_id(cid) or {}
    toggle = "⏸ Выключить" if row.get("enabled") else "▶️ Включить"
    return {
        "inline_keyboard": [
            [{"text": toggle, "callback_data": f"adm:sk:toggle:{cid}"}],
            [{"text": "📤 Отправить сейчас", "callback_data": f"adm:sk:send:{cid}"}],
            [{"text": "🗑 Удалить", "callback_data": f"adm:sk:delask:{cid}"}],
            [{"text": "« К списку", "callback_data": "adm:sk:menu"}],
        ]
    }


def _parks_from_session(sess: dict[str, Any]) -> list[str]:
    raw = sess.get("location_keys") or []
    return [str(x) for x in raw if x]


def _park_picker_text(sess: dict[str, Any]) -> str:
    tag = escape(str(sess.get("tag") or ""))
    selected = _parks_from_session(sess)
    n = len(selected)
    return (
        f"Тег: <code>{tag}</code>\n"
        f"Парки (тикеты + рассылка): <b>{n}</b>\n"
        "Отметьте парки, затем «Далее»."
    )


def _park_picker_keyboard(sess: dict[str, Any]) -> dict:
    selected = set(_parks_from_session(sess))
    rows: list[list[dict[str, str]]] = []
    pair: list[dict[str, str]] = []
    for loc in load_locations():
        key = loc["key"]
        slug = loc["slug"]
        mark = "✅" if key in selected else "⬜"
        btn = {
            "text": f"{mark} {loc['display_name']}",
            "callback_data": f"adm:sk:tg:{slug}",
        }
        pair.append(btn)
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        pair_row(
            {"text": "Все", "callback_data": "adm:sk:all"},
            {"text": "Сброс", "callback_data": "adm:sk:none"},
        )
    )
    rows.append([{"text": "Далее →", "callback_data": "adm:sk:parksdone"}])
    rows.append([{"text": "Отмена", "callback_data": "adm:sk:menu"}])
    return {"inline_keyboard": rows}


def _confirm_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "CONFIRM", "callback_data": "adm:sk:confirm"}],
            [{"text": "Отмена", "callback_data": "adm:sk:menu"}],
        ]
    }


def _slug_to_key(slug: str) -> str | None:
    for loc in load_locations():
        if loc["slug"] == slug:
            return loc["key"]
    return None


def handle_sk_callback(
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
        send(user_id, sk_menu_text(), parse_mode="HTML", reply_markup=sk_menu_keyboard())
        return True

    if action == "item":
        cid = _cid(parts)
        answer(callback_id)
        send(
            user_id,
            _detail_text(cid),
            parse_mode="HTML",
            reply_markup=_detail_keyboard(cid),
        )
        return True

    if action == "toggle":
        cid = _cid(parts)
        row = campaign_by_id(cid)
        if not row:
            answer(callback_id, "нет")
            send(user_id, "СК не найдена.")
            return True
        ok = set_campaign_enabled(cid, not bool(row.get("enabled")))
        answer(callback_id, "ok" if ok else "err")
        send(
            user_id,
            _detail_text(cid),
            parse_mode="HTML",
            reply_markup=_detail_keyboard(cid),
        )
        return True

    if action == "delask":
        cid = _cid(parts)
        row = campaign_by_id(cid)
        answer(callback_id)
        if not row:
            send(user_id, "СК не найдена.")
            return True
        send(
            user_id,
            f"Удалить СК <code>{escape(str(row.get('tag')))}</code>?",
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "🗑 Удалить", "callback_data": f"adm:sk:del:{cid}"}],
                    [{"text": "Отмена", "callback_data": f"adm:sk:item:{cid}"}],
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
            ("🗑 Удалено.\n\n" if ok else "Не удалось удалить.\n\n") + sk_menu_text(),
            parse_mode="HTML",
            reply_markup=sk_menu_keyboard(),
        )
        return True

    if action == "new":
        answer(callback_id)
        set_session(user_id, "sk_tag")
        send(
            user_id,
            "Пришлите <b>тег Tracker</b>, по которому брать тикеты.\n"
            "Очередь всегда <code>SDCFLEETOPS</code>.\n"
            "/cancel — отмена.",
            parse_mode="HTML",
        )
        return True

    if action == "tg" and len(parts) > 3:
        sess = get_session(user_id)
        if not sess or sess.get("kind") != "sk_parks":
            answer(callback_id, "сессия")
            send(user_id, "Начните с «Добавить СК».")
            return True
        key = _slug_to_key(parts[3])
        if not key:
            answer(callback_id, "парк")
            return True
        keys = _parks_from_session(sess)
        if key in keys:
            keys = [k for k in keys if k != key]
        else:
            keys.append(key)
        set_session(user_id, "sk_parks", tag=sess.get("tag"), location_keys=keys)
        sess = get_session(user_id) or {}
        answer(callback_id)
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if action == "all":
        sess = get_session(user_id)
        if not sess or sess.get("kind") != "sk_parks":
            answer(callback_id, "сессия")
            return True
        keys = [loc["key"] for loc in load_locations()]
        set_session(user_id, "sk_parks", tag=sess.get("tag"), location_keys=keys)
        sess = get_session(user_id) or {}
        answer(callback_id)
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if action == "none":
        sess = get_session(user_id)
        if not sess or sess.get("kind") != "sk_parks":
            answer(callback_id, "сессия")
            return True
        set_session(user_id, "sk_parks", tag=sess.get("tag"), location_keys=[])
        sess = get_session(user_id) or {}
        answer(callback_id)
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if action == "parksdone":
        sess = get_session(user_id)
        if not sess or sess.get("kind") != "sk_parks":
            answer(callback_id, "сессия")
            send(user_id, "Начните с «Добавить СК».")
            return True
        keys = _parks_from_session(sess)
        if not keys:
            answer(callback_id, "парки")
            send(user_id, "Выберите хотя бы один парк.")
            return True
        set_session(
            user_id,
            "sk_repeat",
            tag=sess.get("tag"),
            location_keys=keys,
        )
        answer(callback_id)
        send(
            user_id,
            f"Парков: <b>{len(keys)}</b>.\n<b>Нужны повторения?</b>",
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Один раз", "callback_data": "adm:sk:repeat:once"}],
                    [{"text": "Каждый день", "callback_data": "adm:sk:repeat:daily"}],
                    [{"text": "Пн–Пт", "callback_data": "adm:sk:repeat:weekdays"}],
                    [{"text": "Отмена", "callback_data": "adm:sk:menu"}],
                ]
            },
        )
        return True

    if action == "repeat" and len(parts) > 3:
        repeat = parts[3]
        if repeat not in (REPEAT_ONCE, REPEAT_DAILY, REPEAT_WEEKDAYS):
            answer(callback_id, "err")
            return True
        sess = get_session(user_id)
        if not sess or sess.get("kind") != "sk_repeat":
            answer(callback_id, "сессия")
            send(user_id, "Начните с «Добавить СК».")
            return True
        set_session(
            user_id,
            "sk_time",
            tag=sess.get("tag"),
            location_keys=_parks_from_session(sess),
            repeat=repeat,
        )
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
        if not sess or sess.get("kind") != "sk_confirm":
            send(user_id, "Нет черновика. Начните заново.")
            return True
        try:
            row = add_campaign(
                tag=str(sess.get("tag") or ""),
                location_keys=_parks_from_session(sess),
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
            "✅ СК создана и <b>включена</b>\n"
            f"тег: <code>{escape(str(row['tag']))}</code>\n"
            f"время: <code>{row['fire_at']}</code> ({rep})\n"
            f"парков: {len(row.get('location_keys') or [])}",
            parse_mode="HTML",
            reply_markup=sk_menu_keyboard(),
        )
        return True

    if action == "send":
        cid = _cid(parts)
        answer(callback_id, "отправка…")
        threading.Thread(
            target=_send_now_worker,
            args=(cid, user_id, send),
            daemon=True,
            name=f"sk-send-{cid}",
        ).start()
        return True

    answer(callback_id)
    return True


def _send_now_worker(campaign_id: str, user_id: int, send: SendFn) -> None:
    try:
        sent, skipped = send_campaign(campaign_id, mark_state=False)
        send(
            user_id,
            f"📤 СК <code>{escape(campaign_id)}</code>\n"
            f"доставлено: {sent}, пропущено: {skipped}",
            parse_mode="HTML",
        )
    except Exception as e:
        send(user_id, f"❌ Ошибка: {e}")


def handle_sk_text(*, user_id: int, text: str, send: SendFn) -> bool:
    sess = get_session(user_id)
    if not sess:
        return False
    kind = sess.get("kind")

    if kind == "sk_tag":
        tag = text.strip()
        try:
            campaign_query(tag)
        except ValueError as e:
            send(user_id, f"Ошибка тега: {e}")
            return True
        set_session(user_id, "sk_parks", tag=tag, location_keys=[])
        sess = get_session(user_id) or {}
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if kind == "sk_time":
        try:
            from store.schedules import normalize_fire_at

            fire_at = normalize_fire_at(text.strip())
        except ValueError as e:
            send(user_id, f"Ошибка времени: {e}")
            return True
        parks = _parks_from_session(sess)
        repeat = str(sess.get("repeat") or REPEAT_ONCE)
        tag = str(sess.get("tag") or "")
        set_session(
            user_id,
            "sk_confirm",
            tag=tag,
            location_keys=parks,
            repeat=repeat,
            fire_at=fire_at,
        )
        rep = REPEAT_LABELS.get(repeat, repeat)
        park_lines = "\n".join(f"• {escape(_park_label(k))}" for k in parks)
        send(
            user_id,
            "<b>Проверьте СК</b>\n"
            f"тег: <code>{escape(tag)}</code>\n"
            f"время: <code>{fire_at}</code> MSK\n"
            f"повтор: {rep}\n"
            f"парки:\n{park_lines}\n\n"
            "Подтвердите создание:",
            parse_mode="HTML",
            reply_markup=_confirm_keyboard(),
        )
        return True

    return False
