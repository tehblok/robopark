#!/usr/bin/env python3
"""Диспетчер-бот: номер робота → открытые задачи в Tracker.

Только диалог с @ботом: пользователь пишет боту → бот отвечает в этом же чате.
Заявки и админка — только в личке; сервисные чаты (TELEGRAM_CHATS) не используются.

Рассылки — telegram_sender.py, meeting_reminders.py (отдельные скрипты).

Запуск:
  ./scripts/start_dispatcher_bot.sh
  # или: systemctl start tracker-dispatcher
"""

from __future__ import annotations

import atexit
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from html import escape
from pathlib import Path

import requests
from store.secrets import safe_error

from config import DATA_DIR, DISPATCHER_ADMIN_IDS
try:
    from store.secrets import get_telegram_bot_token
    TELEGRAM_BOT_TOKEN = get_telegram_bot_token()
except Exception:
    from credentials import TELEGRAM_BOT_TOKEN
from dispatcher_auth import (
    filter_tasks_for_user,
    get_user_profile,
    has_robot_access,
)
from dispatcher_daily import mark_greeted, prepend_daily_opener
from dispatcher_pending import (
    admin_awaiting_greeting,
    create_pending,
    delete_pending,
    get_pending,
    set_admin_awaiting_greeting,
    set_user_registration_state,
    update_pending,
    user_registration_state,
)
from dispatcher_registration import (
    apply_global,
    apply_location,
    approve_pending,
    format_pending_request,
    greeting_keyboard,
    greeting_text_for,
    location_keyboard,
    pending_ready_to_approve,
)
from dispatcher_roles import (
    allowed_location_tags,
    can_history,
    can_moves,
    can_qr,
    can_zip_wait,
    has_global_robot_search,
    is_admin_user,
    stored_role_label,
)
from dispatcher_users_store import load_all_users
from dispatcher_pause import USER_PAUSED_MSG, admin_help_text, is_paused, set_paused
from admin import (
    admin_home_keyboard,
    admin_home_text,
    handle_admin_callback,
    handle_admin_document,
    handle_admin_text,
)
from admin.keyboards import (
    BTN_ADMIN,
    BTN_HELP,
    BTN_PAUSE,
    BTN_RESUME,
    BTN_ROBOT,
    BTN_STATUS,
    full_admin_reply_keyboard,
    reply_keyboard_for,
    shows_bottom_reply_keyboard,
)
from admin.menu import quick_status_text
from admin.nav import make_edit_send
from admin.ota import deliver_ota_result_notification
from tg_io import lock as _tg_io_lock

from dispatcher_stats import format_stats_report, record_robot_query
from dispatcher_qr import render_yasadr_qr_png, yasadr_code
from dispatcher_sdcwh import format_zip_response, search_delivery_waiting_by_rover
from dispatcher_robomaint import (
    format_active_movements_response,
    format_movement_history_response,
    has_movement_access,
    movement_history_keyboard,
    robot_actions_keyboard,
    robot_view_keyboard,
    search_closed_movements_by_rover,
    search_open_movements_by_rover,
)
from dispatcher_tracker import (
    TELEGRAM_HTML_MAX,
    format_no_access_response,
    format_repair_history_response,
    format_robot_response,
    filter_tasks_for_display,
    parse_robot_number,
    search_closed_repairs_by_rover,
    search_tasks_by_rover,
)

def _api_url() -> str:
    try:
        from store.secrets import get_telegram_bot_token as _tok

        token = (_tok() or "").strip()
    except Exception:
        token = ""
    if not token:
        token = TELEGRAM_BOT_TOKEN
    return f"https://api.telegram.org/bot{token}"
OFFSET_FILE = Path(DATA_DIR) / "dispatcher_bot.offset"
PID_FILE = Path(DATA_DIR) / "dispatcher_bot.pid"

AWAITING_NAME = "awaiting_name"
PENDING_USER_MSG = "Заявка отправлена. Ожидайте подтверждения."
ALREADY_PENDING_MSG = "Заявка уже на рассмотрении."
WELCOME_MSG = "Добро пожаловать"
REJECT_USER_MSG = "Доступ не предоставлен."


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def user_label(user_id: int, profile: dict | None, user: dict) -> str:
    if profile and profile.get("name"):
        return f"{profile['name']} ({user_id})"
    name = user.get("first_name") or user.get("username") or "?"
    return f"{name} ({user_id})"


def plain_preview(text: str, max_len: int = 100) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\n", " · ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


CONNECT_TIMEOUT = 5
LONG_POLL_SEC = 25
GET_UPDATES_READ = LONG_POLL_SEC + 10
SEND_READ = 30
EDIT_READ = 45
CALLBACK_READ = 5
SEND_RETRIES = 3
RETRY_PAUSE_SEC = 1
VIEW_UPDATE_FAILED_HINT = "Не обновилось — нажмите ещё раз"
VIEW_REPLACE_PAUSE_SEC = 0.05

TELEGRAM_UNAVAILABLE = "Сервер не отвечает. Попробуйте позже."
TRACKER_UNAVAILABLE = "Не удалось получить данные. Попробуйте позже."

_user_view_message_ids: dict[int, int] = {}
_user_robot_query_message_ids: dict[int, int] = {}
_tg_session = requests.Session()
USER_MAP_CAP = 800


def remember_user_view_message(user_id: int, message_id: int) -> None:
    _user_view_message_ids[int(user_id)] = message_id
    if len(_user_view_message_ids) > USER_MAP_CAP:
        from store.housekeep import cap_mapping

        cap_mapping(_user_view_message_ids, USER_MAP_CAP)


def remember_user_robot_query_message(user_id: int, message_id: int) -> None:
    _user_robot_query_message_ids[int(user_id)] = message_id
    if len(_user_robot_query_message_ids) > USER_MAP_CAP:
        from store.housekeep import cap_mapping

        cap_mapping(_user_robot_query_message_ids, USER_MAP_CAP)


def get_user_view_message_id(user_id: int) -> int | None:
    return _user_view_message_ids.get(user_id)


def is_bot_dialog(msg: dict) -> bool:
    chat = msg.get("chat") or {}
    user = msg.get("from") or {}
    user_id = user.get("id")
    if not user_id or user.get("is_bot"):
        return False
    if chat.get("type") != "private":
        return False
    return chat.get("id") == user_id


def is_private_user(user_id: int, chat_id: int) -> bool:
    return chat_id > 0 and chat_id == user_id


def send_private(
    user_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: dict | None = None,
    reply_to_message_id: int | None = None,
    retries: int | None = None,
) -> int | None:
    if not is_private_user(user_id, user_id):
        log(f"✗ блок отправки: user_id={user_id}")
        return None

    payload: dict = {
        "chat_id": user_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    attempts = retries if retries is not None else SEND_RETRIES
    with _tg_io_lock:
        for attempt in range(1, attempts + 1):
            try:
                r = _tg_session.post(
                    f"{_api_url()}/sendMessage",
                    json=payload,
                    timeout=(CONNECT_TIMEOUT, SEND_READ),
                )
                if r.ok:
                    result = r.json().get("result") or {}
                    msg_id = result.get("message_id")
                    return int(msg_id) if msg_id is not None else None
                desc = ""
                try:
                    desc = str((r.json() or {}).get("description") or "")
                except Exception:
                    desc = ""
                # HTML/entity parse errors: retry once without parse_mode
                if parse_mode and "parse" in desc.lower():
                    payload.pop("parse_mode", None)
                    parse_mode = None
                    continue
                if attempt == attempts:
                    log(f"✗ не отправлено user {user_id}: HTTP {r.status_code} {desc[:180]}")
            except requests.exceptions.RequestException as e:
                if attempt == attempts:
                    log(f"✗ не отправлено user {user_id}: {safe_error(e)}")
            if attempt < attempts:
                time.sleep(RETRY_PAUSE_SEC)
    return None


def edit_private_message(
    chat_id: int,
    message_id: int,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: dict | None = None,
    retries: int | None = None,
) -> bool:
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    attempts = retries if retries is not None else SEND_RETRIES
    with _tg_io_lock:
        for attempt in range(1, attempts + 1):
            try:
                r = _tg_session.post(
                    f"{_api_url()}/editMessageText",
                    json=payload,
                    timeout=(CONNECT_TIMEOUT, EDIT_READ),
                )
                if r.ok:
                    return True
                body = r.text.lower()
                if r.status_code == 400 and "message is not modified" in body:
                    return True
                if parse_mode and r.status_code == 400 and "parse" in body:
                    payload.pop("parse_mode", None)
                    parse_mode = None
                    continue
                # 4xx besides flood: retrying "message is too long" only keeps the spinner.
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    log(
                        f"✗ editMessage chat={chat_id} msg={message_id}: "
                        f"HTTP {r.status_code} {r.text[:120]}"
                    )
                    return False
                if attempt == attempts:
                    log(
                        f"✗ editMessage chat={chat_id} msg={message_id}: "
                        f"HTTP {r.status_code} {r.text[:120]}"
                    )
            except requests.exceptions.RequestException as e:
                if attempt == attempts:
                    log(f"✗ editMessage chat={chat_id} msg={message_id}: {safe_error(e)}")
            if attempt < attempts:
                time.sleep(RETRY_PAUSE_SEC)
    return False


def _clip_tg_html(text: str) -> str:
    if len(text) <= TELEGRAM_HTML_MAX:
        return text
    return text[: TELEGRAM_HTML_MAX - 30].rstrip() + "\n\n… обрезано"


def _replace_loading_message(
    user_id: int,
    loading_msg_id: int,
    text: str,
    keyboard: dict | None = None,
) -> bool:
    """Always replace the ⏳ spinner — never leave it if the view id moved."""
    text = _clip_tg_html(text)
    if edit_private_message(
        user_id,
        loading_msg_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    ):
        remember_user_view_message(user_id, loading_msg_id)
        return True
    new_id = send_private(
        user_id,
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    if new_id:
        remember_user_view_message(user_id, new_id)
        edit_private_message(
            user_id,
            loading_msg_id,
            "Готово — ответ ниже.",
        )
        return True
    return False


def edit_message_reply_markup(
    chat_id: int,
    message_id: int,
    reply_markup: dict | None,
) -> bool:
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup or {"inline_keyboard": []},
    }
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            r = _tg_session.post(
                f"{_api_url()}/editMessageReplyMarkup",
                json=payload,
                timeout=(CONNECT_TIMEOUT, EDIT_READ),
            )
            if r.ok:
                return True
            body = r.text.lower()
            if r.status_code == 400 and "message is not modified" in body:
                return True
            if attempt == SEND_RETRIES:
                log(f"✗ editMarkup chat={chat_id} msg={message_id}: HTTP {r.status_code}")
        except requests.exceptions.RequestException as e:
            if attempt == SEND_RETRIES:
                log(f"✗ editMarkup chat={chat_id} msg={message_id}: {safe_error(e)}")
        if attempt < SEND_RETRIES:
            time.sleep(RETRY_PAUSE_SEC)
    return False


def notify_view_update_failed(callback_id: str) -> None:
    answer_callback(callback_id, VIEW_UPDATE_FAILED_HINT)


def delete_private_message(chat_id: int, message_id: int) -> bool:
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            r = _tg_session.post(
                f"{_api_url()}/deleteMessage",
                json={"chat_id": chat_id, "message_id": message_id},
                timeout=(CONNECT_TIMEOUT, SEND_READ),
            )
            if r.ok:
                return True
            body = r.text.lower()
            if r.status_code == 400 and "message to delete not found" in body:
                return True
            if attempt == SEND_RETRIES:
                log(f"✗ deleteMessage chat={chat_id} msg={message_id}: HTTP {r.status_code}")
        except requests.exceptions.RequestException as e:
            if attempt == SEND_RETRIES:
                log(f"✗ deleteMessage chat={chat_id} msg={message_id}: {safe_error(e)}")
        if attempt < SEND_RETRIES:
            time.sleep(RETRY_PAUSE_SEC)
    return False


def clear_robot_session(user_id: int) -> None:
    view_id = _user_view_message_ids.pop(user_id, None)
    query_id = _user_robot_query_message_ids.pop(user_id, None)
    deleted = False
    if view_id is not None:
        deleted = delete_private_message(user_id, view_id) or deleted
    if query_id is not None:
        deleted = delete_private_message(user_id, query_id) or deleted
    if deleted:
        time.sleep(VIEW_REPLACE_PAUSE_SEC)


def edit_private_message_photo(
    chat_id: int,
    message_id: int,
    photo: bytes,
    *,
    caption: str | None = None,
    reply_markup: dict | None = None,
    retries: int | None = None,
) -> bool:
    media: dict = {"type": "photo", "media": "attach://photo"}
    if caption:
        media["caption"] = caption

    data: dict = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "media": json.dumps(media),
    }
    if reply_markup is not None:
        data["reply_markup"] = json.dumps(reply_markup)

    attempts = retries if retries is not None else SEND_RETRIES
    for attempt in range(1, attempts + 1):
        try:
            r = _tg_session.post(
                f"{_api_url()}/editMessageMedia",
                data=data,
                files={"photo": ("qr.png", photo, "image/png")},
                timeout=(CONNECT_TIMEOUT, EDIT_READ),
            )
            if r.ok:
                return True
            body = r.text.lower()
            if r.status_code == 400 and "message is not modified" in body:
                return True
            if attempt == attempts:
                log(
                    f"✗ editMessageMedia chat={chat_id} msg={message_id}: "
                    f"HTTP {r.status_code} {r.text[:120]}"
                )
        except requests.exceptions.RequestException as e:
            if attempt == attempts:
                log(f"✗ editMessageMedia chat={chat_id} msg={message_id}: {safe_error(e)}")
        if attempt < attempts:
            time.sleep(RETRY_PAUSE_SEC)
    return False


def update_user_view(
    cb: dict,
    user_id: int,
    text: str,
    *,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> bool:
    msg = cb.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    if chat_id and message_id:
        if msg.get("photo"):
            if delete_private_message(chat_id, message_id):
                new_id = send_private(
                    user_id,
                    text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    retries=SEND_RETRIES,
                )
                if new_id:
                    remember_user_view_message(user_id, new_id)
                    return True
            return False
        if edit_private_message(
            chat_id,
            message_id,
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        ):
            remember_user_view_message(user_id, message_id)
            return True
        # Тяжёлый текст (ЗИП): сначала текст, потом клавиатура отдельным запросом.
        if edit_private_message(
            chat_id,
            message_id,
            text,
            parse_mode=parse_mode,
            reply_markup=None,
        ):
            if reply_markup:
                ok = edit_message_reply_markup(chat_id, message_id, reply_markup)
            else:
                ok = True
            if ok:
                remember_user_view_message(user_id, message_id)
            return ok
        return False
    new_id = send_private(
        user_id,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        retries=SEND_RETRIES,
    )
    if new_id:
        remember_user_view_message(user_id, new_id)
        return True
    return False


def update_user_view_photo(
    cb: dict,
    user_id: int,
    photo: bytes,
    *,
    caption: str | None = None,
    reply_markup: dict | None = None,
) -> bool:
    msg = cb.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    if chat_id and message_id:
        if edit_private_message_photo(
            chat_id,
            message_id,
            photo,
            caption=caption,
            reply_markup=reply_markup,
        ):
            remember_user_view_message(user_id, message_id)
            return True
        if edit_private_message_photo(
            chat_id,
            message_id,
            photo,
            caption=caption,
            reply_markup=None,
        ):
            if reply_markup:
                ok = edit_message_reply_markup(chat_id, message_id, reply_markup)
            else:
                ok = True
            if ok:
                remember_user_view_message(user_id, message_id)
            return ok
        return False
    new_id = send_private_photo(
        user_id,
        photo,
        caption=caption,
        retries=SEND_RETRIES,
    )
    if new_id:
        remember_user_view_message(user_id, new_id)
        return True
    return False


def send_private_photo(
    user_id: int,
    photo: bytes,
    *,
    caption: str | None = None,
    retries: int | None = None,
) -> int | None:
    if not is_private_user(user_id, user_id):
        log(f"✗ блок отправки фото: user_id={user_id}")
        return None

    data: dict = {"chat_id": user_id}
    if caption:
        data["caption"] = caption

    attempts = retries if retries is not None else SEND_RETRIES
    for attempt in range(1, attempts + 1):
        try:
            r = _tg_session.post(
                f"{_api_url()}/sendPhoto",
                data=data,
                files={"photo": ("qr.png", photo, "image/png")},
                timeout=(CONNECT_TIMEOUT, SEND_READ),
            )
            if r.ok:
                result = r.json().get("result") or {}
                msg_id = result.get("message_id")
                return int(msg_id) if msg_id is not None else None
            if attempt == attempts:
                log(f"✗ фото не отправлено user {user_id}: HTTP {r.status_code}")
        except requests.exceptions.RequestException as e:
            if attempt == attempts:
                log(f"✗ фото не отправлено user {user_id}: {safe_error(e)}")
        if attempt < attempts:
            time.sleep(RETRY_PAUSE_SEC)
    return None


def send_private_document(
    user_id: int,
    file_path: Path | str,
    *,
    caption: str | None = None,
    retries: int | None = None,
) -> bool:
    path = Path(file_path)
    if not path.is_file():
        log(f"✗ документ не найден: {path}")
        return False
    if not is_private_user(user_id, user_id):
        log(f"✗ блок отправки документа: user_id={user_id}")
        return False

    data: dict = {"chat_id": user_id}
    if caption:
        data["caption"] = caption

    attempts = retries if retries is not None else SEND_RETRIES
    with _tg_io_lock:
        for attempt in range(1, attempts + 1):
            try:
                with path.open("rb") as fh:
                    r = _tg_session.post(
                        f"{_api_url()}/sendDocument",
                        data=data,
                        files={"document": (path.name, fh, "application/zip")},
                        timeout=(CONNECT_TIMEOUT, 180),
                    )
                if r.ok:
                    return True
                if attempt == attempts:
                    log(f"✗ документ не отправлен user {user_id}: HTTP {r.status_code}")
            except requests.exceptions.RequestException as e:
                if attempt == attempts:
                    log(f"✗ документ не отправлен user {user_id}: {safe_error(e)}")
            if attempt < attempts:
                time.sleep(RETRY_PAUSE_SEC)
    return False


def answer_callback(callback_id: str, text: str | None = None) -> None:
    if not callback_id:
        return
    payload: dict = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
        # Keep toast short so Telegram dismisses spinner quickly
        if len(payload["text"]) > 180:
            payload["text"] = payload["text"][:177] + "…"
    try:
        _tg_session.post(
            f"{_api_url()}/answerCallbackQuery",
            json=payload,
            timeout=(CONNECT_TIMEOUT, CALLBACK_READ),
        )
    except requests.exceptions.RequestException as e:
        log(f"✗ answerCallbackQuery: {safe_error(e)}")


def get_updates(offset: int) -> list[dict]:
    try:
        r = _tg_session.get(
            f"{_api_url()}/getUpdates",
            params={
                "offset": offset,
                "timeout": LONG_POLL_SEC,
                "limit": 50,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=(CONNECT_TIMEOUT, GET_UPDATES_READ),
        )
        if r.ok:
            return r.json().get("result", [])
        log(f"✗ Telegram getUpdates: {r.status_code}")
    except requests.exceptions.RequestException as e:
        log(f"✗ Telegram getUpdates: {safe_error(e)}")
    return []


def reply_in_bot(
    bot_chat_id: int,
    user_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    parse_mode: str | None = None,
    reply_markup: dict | None = None,
    retries: int | None = None,
    notify_on_fail: bool = False,
) -> bool:
    ok = send_private(
        user_id,
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        reply_to_message_id=reply_to_message_id,
        retries=retries,
    )
    if not ok and notify_on_fail:
        send_private(user_id, TELEGRAM_UNAVAILABLE, reply_to_message_id=reply_to_message_id, retries=1)
    return ok


def cmd_name(text: str) -> str:
    t = text.strip().lower().split()[0] if text.strip() else ""
    if "@" in t:
        t = t.split("@", 1)[0]
    return t


HELP_TEXT = (
    "• <b>🔍 Робот</b> — номер (1842 или a1842) → задачи\n"
    "• под ответом — перемещения и история ремонтов\n"
    "• /help — эта справка"
)

HELP_TEXT_MECHANIC = (
    "• напишите номер робота (1842 или a1842) → задачи\n"
    "• под ответом — перемещения, история и QR\n"
    "• /help — эта справка"
)


def help_text_for(user_id: int) -> str:
    if is_admin_user(user_id):
        return HELP_TEXT + "\n\n<b>Админ:</b> ⚙️ Управление · 📊 Статус · ⏸/▶️"
    if shows_bottom_reply_keyboard(reply_keyboard_for(user_id)):
        return HELP_TEXT
    return HELP_TEXT_MECHANIC


def log_outgoing(who: str, label: str, text: str) -> None:
    log(f"{who} ← {label}: {plain_preview(text, 100)}")


def notify_admins_new_request(target_user_id: int, entry: dict) -> None:
    text = format_pending_request(entry, target_user_id)
    keyboard = location_keyboard(target_user_id)
    try:
        from store.roles import admin_user_ids

        admin_ids = admin_user_ids()
    except Exception:
        admin_ids = list(DISPATCHER_ADMIN_IDS)
    for admin_id in admin_ids:
        send_private(admin_id, text, parse_mode="HTML", reply_markup=keyboard)


def notify_admins_greeting_step(target_user_id: int, entry: dict) -> None:
    text = format_pending_request(entry, target_user_id)
    text += "\n\nВыберите приветствие:"
    keyboard = greeting_keyboard(target_user_id)
    try:
        from store.roles import admin_user_ids

        admin_ids = admin_user_ids()
    except Exception:
        admin_ids = list(DISPATCHER_ADMIN_IDS)
    for admin_id in admin_ids:
        send_private(admin_id, text, parse_mode="HTML", reply_markup=keyboard)


def welcome_approved_user(target_user_id: int, entry: dict, who: str = "") -> None:
    kb = reply_keyboard_for(target_user_id)
    send_private(target_user_id, WELCOME_MSG, reply_markup=kb)
    greeting = greeting_text_for(entry)
    send_private(target_user_id, greeting, reply_markup=kb)
    mark_greeted(target_user_id)
    label = who or entry.get("display_name") or str(target_user_id)
    log_outgoing(label, "добро пожаловать", WELCOME_MSG)
    log_outgoing(label, "приветствие", greeting)


def start_registration(bot_chat_id: int, user_id: int, reply_to: int | None) -> None:
    if get_pending(user_id):
        reply_in_bot(bot_chat_id, user_id, ALREADY_PENDING_MSG, reply_to_message_id=reply_to)
        return
    set_user_registration_state(user_id, AWAITING_NAME)
    prompt = "Как к Вам обращаться?"
    reply_in_bot(
        bot_chat_id,
        user_id,
        prompt,
        reply_to_message_id=reply_to,
    )
    log_outgoing(str(user_id), "регистрация", prompt)


def complete_registration(
    bot_chat_id: int,
    user_id: int,
    display_name: str,
    user: dict,
    reply_to: int | None,
) -> None:
    set_user_registration_state(user_id, None)
    username = user.get("username")
    first_name = user.get("first_name")
    entry = create_pending(
        user_id,
        display_name=display_name.strip(),
        username=username,
        first_name=first_name,
    )
    notify_admins_new_request(user_id, entry)
    reply_in_bot(bot_chat_id, user_id, PENDING_USER_MSG, reply_to_message_id=reply_to)
    log(f"Заявка: {display_name} ({user_id}) → админам")


def reject_user(target_user_id: int) -> None:
    delete_pending(target_user_id)
    set_user_registration_state(target_user_id, None)
    send_private(target_user_id, REJECT_USER_MSG)


def finalize_approval(admin_id: int, target_user_id: int) -> str | None:
    entry = get_pending(target_user_id)
    if not entry or not pending_ready_to_approve(entry):
        return "Заявка не готова: выберите локацию и приветствие."
    profile = approve_pending(target_user_id)
    if not profile:
        return "Не удалось одобрить заявку."
    set_admin_awaiting_greeting(admin_id, None)
    welcome_approved_user(target_user_id, entry)
    name = entry.get("display_name") or str(target_user_id)
    log(f"✅ Доступ: {name} ({target_user_id})")
    return None


def handle_admin_custom_greeting(
    admin_id: int,
    target_user_id: int,
    text: str,
    reply_to: int | None,
) -> None:
    entry = get_pending(target_user_id)
    if not entry or not entry.get("allowed_tags"):
        set_admin_awaiting_greeting(admin_id, None)
        send_private(admin_id, "Заявка не найдена или локация не выбрана.")
        return
    update_pending(
        target_user_id,
        greeting_mode="custom",
        custom_greeting=text.strip(),
    )
    entry = get_pending(target_user_id)
    err = finalize_approval(admin_id, target_user_id)
    if err:
        send_private(admin_id, err, reply_to_message_id=reply_to)
    else:
        send_private(admin_id, f"✅ Одобрено: {entry.get('display_name')} ({target_user_id})")


def process_callback_query(cb: dict) -> None:
    user = cb.get("from") or {}
    user_id = user.get("id")
    callback_id = cb.get("id", "")
    if not user_id:
        answer_callback(callback_id)
        return

    data = (cb.get("data") or "").strip()

    if data.startswith("hist:"):
        handle_repair_history_callback(cb, user_id, data)
        return
    if data.startswith("move:"):
        handle_movement_callback(cb, user_id, data)
        return
    if data.startswith("mhist:"):
        handle_movement_history_callback(cb, user_id, data)
        return
    if data.startswith("qr:"):
        handle_qr_callback(cb, user_id, data)
        return
    if data.startswith("zip:"):
        handle_zip_callback(cb, user_id, data)
        return
    if data.startswith("tasks:"):
        handle_tasks_callback(cb, user_id, data)
        return

    if data.startswith("adm:"):
        if not is_admin_user(user_id):
            answer_callback(callback_id, "Нет прав")
            return
        answer_callback(callback_id)

        msg = cb.get("message") or {}
        mid = msg.get("message_id")

        def _send_new(uid, body, reply_markup=None, parse_mode=None, **kwargs):
            send_private(uid, body, reply_markup=reply_markup, parse_mode=parse_mode)

        _send = make_edit_send(user_id, mid, _send_new, edit_private_message)

        def _answer(_cid, _text=None):
            return

        def _send_document(uid, path, caption=None):
            return send_private_document(uid, path, caption=caption)

        handle_admin_callback(
            user_id=user_id,
            data=data,
            callback_id=callback_id,
            send=_send,
            answer=_answer,
            send_document=_send_document,
            send_new=_send_new,
        )
        return

    admin_id = user_id
    if not is_admin_user(admin_id):
        answer_callback(cb["id"], "Нет прав")
        return
    parts = data.split(":")
    if len(parts) < 2:
        answer_callback(cb["id"])
        return

    action = parts[0]
    try:
        target_user_id = int(parts[1])
    except ValueError:
        answer_callback(cb["id"])
        return

    if action == "rej":
        reject_user(target_user_id)
        answer_callback(cb["id"], "Отклонено")
        send_private(admin_id, f"❌ Отклонено: id {target_user_id}")
        return

    if action == "loc" and len(parts) == 3:
        entry = apply_location(target_user_id, parts[2])
        if not entry:
            answer_callback(cb["id"], "Неизвестная локация")
            return
        answer_callback(cb["id"], entry["location_label"])
        notify_admins_greeting_step(target_user_id, entry)
        return

    if action == "glob":
        entry = apply_global(target_user_id)
        if not entry:
            answer_callback(cb["id"], "Ошибка")
            return
        answer_callback(cb["id"], "Global")
        notify_admins_greeting_step(target_user_id, entry)
        return

    if action == "grdef":
        entry = update_pending(target_user_id, greeting_mode="default")
        if not entry:
            answer_callback(cb["id"], "Заявка не найдена")
            return
        err = finalize_approval(admin_id, target_user_id)
        if err:
            answer_callback(cb["id"], err)
            send_private(admin_id, err)
        else:
            answer_callback(cb["id"], "Одобрено")
            send_private(admin_id, f"✅ Одобрено: {entry.get('display_name')} ({target_user_id})")
        return

    if action == "grcust":
        entry = get_pending(target_user_id)
        if not entry or not entry.get("allowed_tags"):
            answer_callback(cb["id"], "Сначала выберите локацию")
            return
        set_admin_awaiting_greeting(admin_id, target_user_id)
        answer_callback(cb["id"])
        name = entry.get("display_name") or "?"
        send_private(
            admin_id,
            f"Введите приветствие для <b>{escape(name)}</b> (id <code>{target_user_id}</code>).\n"
            "Можно использовать <code>{{name}}</code>.\n"
            "Отправьте текст следующим сообщением.",
            parse_mode="HTML",
        )
        return

    answer_callback(cb["id"])


def _parse_rover_callback(data: str, prefix: str) -> str | None:
    if not data.startswith(prefix):
        return None
    rover = data[len(prefix):].strip().lower()
    if not rover or not re.fullmatch(r"a\d{1,6}", rover):
        return None
    return rover


def _robot_action_flags(user_id: int, profile: dict) -> dict[str, bool]:
    try:
        from store.locations import any_participation

        return {
            "show_zip": can_zip_wait(user_id, profile) and any_participation("sdcwh_zip"),
            "show_move": can_moves(user_id, profile) and any_participation("robomaint_moves"),
            "show_hist": can_history(user_id, profile),
            "show_qr": can_qr(user_id, profile),
        }
    except Exception:
        return {
            "show_zip": has_global_robot_search(user_id, profile),
            "show_move": True,
            "show_hist": True,
            "show_qr": True,
        }


def build_robot_tasks_view(
    user_id: int,
    profile: dict,
    rover: str,
    tasks: list[dict],
) -> tuple[str, dict | None]:
    flags = _robot_action_flags(user_id, profile)
    show_zip = flags["show_zip"]
    show_move = flags["show_move"]
    show_hist = flags["show_hist"]
    show_qr = flags["show_qr"]
    if not tasks:
        text = format_robot_response(rover, [])
        keyboard = (
            robot_actions_keyboard(
                rover,
                show_zip=show_zip,
                show_move=show_move,
                show_hist=show_hist,
                show_qr=show_qr,
            )
            if has_robot_access(user_id, profile, tasks)
            else None
        )
        return text, keyboard

    if not has_robot_access(user_id, profile, tasks):
        allowed = allowed_location_tags(profile)
        if has_global_robot_search(user_id, profile):
            label = "полный доступ"
        else:
            label = ", ".join(allowed) if allowed else "локация не задана"
        return format_no_access_response(rover, label), None

    visible = filter_tasks_for_user(user_id, profile, tasks)
    visible = filter_tasks_for_display(visible, rover)
    text = format_robot_response(rover, visible)
    keyboard = robot_actions_keyboard(
        rover,
        show_zip=show_zip,
        show_move=show_move,
        show_hist=show_hist,
        show_qr=show_qr,
    )
    return text, keyboard


def handle_tasks_callback(cb: dict, user_id: int, data: str) -> None:
    callback_id = cb["id"]
    rover = _parse_rover_callback(data, "tasks:")
    if not rover:
        answer_callback(callback_id, "Неверный запрос")
        return

    profile = get_user_profile(user_id)
    if not profile:
        answer_callback(callback_id, "Нет доступа")
        return

    if is_paused() and not is_admin_user(user_id):
        answer_callback(callback_id)
        send_private(user_id, USER_PAUSED_MSG)
        return

    user = cb.get("from") or {}
    who = user_label(user_id, profile, user)
    answer_callback(callback_id, "Загружаю…")

    t0 = time.time()
    tasks, err = search_tasks_by_rover(rover)
    elapsed = time.time() - t0
    if err:
        update_user_view(cb, user_id, TRACKER_UNAVAILABLE)
        log(f"{who} ← tasks {rover}: ошибка Tracker ({elapsed:.1f}с) — {err}")
        return

    text, keyboard = build_robot_tasks_view(user_id, profile, rover, tasks)
    if update_user_view(cb, user_id, text, reply_markup=keyboard):
        log(f"{who} ← tasks {rover}: к задачам ({elapsed:.1f}с)")
    else:
        notify_view_update_failed(callback_id)


def handle_repair_history_callback(cb: dict, user_id: int, data: str) -> None:
    callback_id = cb["id"]
    rover = _parse_rover_callback(data, "hist:")
    if not rover:
        answer_callback(callback_id, "Неверный запрос")
        return

    profile = get_user_profile(user_id)
    if not profile:
        answer_callback(callback_id, "Нет доступа")
        return

    if not can_history(user_id, profile):
        answer_callback(callback_id, "Нет доступа")
        return

    if is_paused() and not is_admin_user(user_id):
        answer_callback(callback_id)
        send_private(user_id, USER_PAUSED_MSG)
        return

    user = cb.get("from") or {}
    who = user_label(user_id, profile, user)
    answer_callback(callback_id, "Загружаю…")

    t0 = time.time()
    tasks, err = search_closed_repairs_by_rover(rover)
    elapsed = time.time() - t0
    flags = _robot_action_flags(user_id, profile)
    if err:
        update_user_view(cb, user_id, TRACKER_UNAVAILABLE)
        log(f"{who} ← hist {rover}: ошибка Tracker ({elapsed:.1f}с) — {err}")
        return

    keyboard = None
    if not has_robot_access(user_id, profile, tasks):
        text = format_no_access_response(rover, "")
        summary = "история: нет доступа"
    else:
        visible = filter_tasks_for_user(user_id, profile, tasks)
        visible = filter_tasks_for_display(visible, rover)
        text = format_repair_history_response(rover, visible)
        keyboard = robot_view_keyboard(rover, show_back=True, **flags)
        n = len(visible)
        summary = f"история: {n} {'ремонт' if n == 1 else 'ремонта' if 2 <= n <= 4 else 'ремонтов'}"

    if update_user_view(cb, user_id, text, reply_markup=keyboard):
        preview = plain_preview(text, 80)
        log(f"{who} ← hist {rover}: {summary} ({elapsed:.1f}с) — {preview}")
    else:
        notify_view_update_failed(callback_id)
        log(f"{who} ← hist {rover}: {summary} — не доставлено")


def handle_movement_callback(cb: dict, user_id: int, data: str) -> None:
    callback_id = cb["id"]
    rover = _parse_rover_callback(data, "move:")
    if not rover:
        answer_callback(callback_id, "Неверный запрос")
        return

    profile = get_user_profile(user_id)
    if not profile:
        answer_callback(callback_id, "Нет доступа")
        return

    if not _robot_action_flags(user_id, profile)["show_move"]:
        answer_callback(callback_id, "Нет доступа")
        return

    if is_paused() and not is_admin_user(user_id):
        answer_callback(callback_id)
        send_private(user_id, USER_PAUSED_MSG)
        return

    user = cb.get("from") or {}
    who = user_label(user_id, profile, user)
    answer_callback(callback_id, "Загружаю…")

    t0 = time.time()
    tasks, err = search_open_movements_by_rover(rover)
    elapsed = time.time() - t0
    flags = _robot_action_flags(user_id, profile)
    if err:
        update_user_view(cb, user_id, TRACKER_UNAVAILABLE)
        log(f"{who} ← move {rover}: ошибка Tracker ({elapsed:.1f}с) — {err}")
        return

    keyboard = None
    if not has_movement_access(user_id, profile, rover, tasks):
        text = format_no_access_response(rover, "")
        summary = "перемещение: нет доступа"
    else:
        text = format_active_movements_response(rover, tasks)
        keyboard = robot_view_keyboard(
            rover,
            show_back=True,
            extra_rows=movement_history_keyboard(rover)["inline_keyboard"],
            **flags,
        )
        n = len(tasks)
        summary = f"перемещение: {n} {'тикет' if n == 1 else 'тикета' if 2 <= n <= 4 else 'тикетов'}"

    if update_user_view(cb, user_id, text, reply_markup=keyboard):
        preview = plain_preview(text, 80)
        log(f"{who} ← move {rover}: {summary} ({elapsed:.1f}с) — {preview}")
    else:
        notify_view_update_failed(callback_id)
        log(f"{who} ← move {rover}: {summary} — не доставлено")


def handle_movement_history_callback(cb: dict, user_id: int, data: str) -> None:
    callback_id = cb["id"]
    rover = _parse_rover_callback(data, "mhist:")
    if not rover:
        answer_callback(callback_id, "Неверный запрос")
        return

    profile = get_user_profile(user_id)
    if not profile:
        answer_callback(callback_id, "Нет доступа")
        return

    if not _robot_action_flags(user_id, profile)["show_move"]:
        answer_callback(callback_id, "Нет доступа")
        return

    if is_paused() and not is_admin_user(user_id):
        answer_callback(callback_id)
        send_private(user_id, USER_PAUSED_MSG)
        return

    user = cb.get("from") or {}
    who = user_label(user_id, profile, user)
    answer_callback(callback_id, "Загружаю…")

    t0 = time.time()
    tasks, err = search_closed_movements_by_rover(rover)
    elapsed = time.time() - t0
    flags = _robot_action_flags(user_id, profile)
    if err:
        update_user_view(cb, user_id, TRACKER_UNAVAILABLE)
        log(f"{who} ← mhist {rover}: ошибка Tracker ({elapsed:.1f}с) — {err}")
        return

    keyboard = None
    if not has_movement_access(user_id, profile, rover, tasks):
        text = format_no_access_response(rover, "")
        summary = "ист. перемещений: нет доступа"
    else:
        text = format_movement_history_response(rover, tasks)
        keyboard = robot_view_keyboard(rover, show_back=True, **flags)
        n = len(tasks)
        summary = f"ист. перемещений: {n}"

    if update_user_view(cb, user_id, text, reply_markup=keyboard):
        preview = plain_preview(text, 80)
        log(f"{who} ← mhist {rover}: {summary} ({elapsed:.1f}с) — {preview}")
    else:
        notify_view_update_failed(callback_id)
        log(f"{who} ← mhist {rover}: {summary} — не доставлено")


def handle_qr_callback(cb: dict, user_id: int, data: str) -> None:
    callback_id = cb["id"]
    rover = _parse_rover_callback(data, "qr:")
    if not rover:
        answer_callback(callback_id, "Неверный запрос")
        return

    profile = get_user_profile(user_id)
    if not profile:
        answer_callback(callback_id, "Нет доступа")
        return

    if not can_qr(user_id, profile):
        answer_callback(callback_id, "Нет доступа")
        return

    if is_paused() and not is_admin_user(user_id):
        answer_callback(callback_id)
        send_private(user_id, USER_PAUSED_MSG)
        return

    user = cb.get("from") or {}
    who = user_label(user_id, profile, user)
    answer_callback(callback_id, "Генерирую…")

    t0 = time.time()
    tasks, err = search_tasks_by_rover(rover)
    elapsed = time.time() - t0
    if err:
        update_user_view(cb, user_id, TRACKER_UNAVAILABLE)
        log(f"{who} ← qr {rover}: ошибка Tracker ({elapsed:.1f}с) — {err}")
        return

    if not has_robot_access(user_id, profile, tasks):
        update_user_view(cb, user_id, format_no_access_response(rover, ""))
        log(f"{who} ← qr {rover}: нет доступа ({elapsed:.1f}с)")
        return

    code = yasadr_code(rover)
    try:
        png = render_yasadr_qr_png(rover)
    except Exception as e:
        notify_view_update_failed(callback_id)
        log(f"{who} ← qr {rover}: ошибка генерации — {safe_error(e)}")
        return

    flags = _robot_action_flags(user_id, profile)
    keyboard = robot_view_keyboard(rover, show_back=True, **flags)

    if update_user_view_photo(cb, user_id, png, caption=code, reply_markup=keyboard):
        log(f"{who} ← qr {rover}: {code} ({elapsed:.1f}с)")
    else:
        notify_view_update_failed(callback_id)
        log(f"{who} ← qr {rover}: {code} — не доставлено")


def handle_zip_callback(cb: dict, user_id: int, data: str) -> None:
    callback_id = cb["id"]
    rover = _parse_rover_callback(data, "zip:")
    if not rover:
        answer_callback(callback_id, "Неверный запрос")
        return

    profile = get_user_profile(user_id)
    if not profile:
        answer_callback(callback_id, "Нет доступа")
        return

    if not _robot_action_flags(user_id, profile)["show_zip"]:
        answer_callback(callback_id, "Нет доступа")
        return

    if is_paused() and not is_admin_user(user_id):
        answer_callback(callback_id)
        send_private(user_id, USER_PAUSED_MSG)
        return

    user = cb.get("from") or {}
    who = user_label(user_id, profile, user)
    answer_callback(callback_id, "Загружаю…")

    t0 = time.time()
    issues, err = search_delivery_waiting_by_rover(rover)
    elapsed = time.time() - t0
    if err:
        update_user_view(cb, user_id, TRACKER_UNAVAILABLE)
        log(f"{who} ← zip {rover}: ошибка Tracker ({elapsed:.1f}с) — {err}")
        return

    text = format_zip_response(rover, issues)
    flags = dict(_robot_action_flags(user_id, profile))
    flags["show_zip"] = True
    keyboard = robot_view_keyboard(rover, show_back=True, **flags)
    n = len(issues)
    summary = f"ЗИП: {n} {'тикет' if n == 1 else 'тикета' if 2 <= n <= 4 else 'тикетов'}"

    if update_user_view(cb, user_id, text, reply_markup=keyboard):
        preview = plain_preview(text, 80)
        log(f"{who} ← zip {rover}: {summary} ({elapsed:.1f}с) — {preview}")
    else:
        notify_view_update_failed(callback_id)
        log(f"{who} ← zip {rover}: {summary} — не доставлено")


def reply_with_daily_opener(
    bot_chat_id: int,
    user_id: int,
    profile: dict,
    body: str,
    reply_to: int | None,
    *,
    who: str = "",
    for_robot_query: bool = False,
    parse_mode: str | None = None,
    notify_on_fail: bool = False,
    reply_markup: dict | None = None,
) -> bool:
    text, had_opener, opener = prepend_daily_opener(
        user_id,
        profile,
        body,
        for_robot_query=for_robot_query,
    )
    kb = reply_markup if reply_markup is not None else reply_keyboard_for(user_id)
    if reply_in_bot(
        bot_chat_id,
        user_id,
        text,
        reply_to_message_id=reply_to,
        parse_mode=parse_mode,
        reply_markup=kb,
        notify_on_fail=notify_on_fail,
    ):
        if had_opener and opener:
            mark_greeted(user_id)
            label = who or profile.get("name") or str(user_id)
            log_outgoing(label, "приветствие", opener)
        return True
    return False


def _ensure_admin_profile(user_id: int, profile: dict | None) -> dict:
    """Full-admins always have a stored operator profile (never registration)."""
    if profile:
        return profile
    from config import DISPATCHER_USERS
    from dispatcher_users_store import save_user

    uid = int(user_id)
    seeded = DISPATCHER_USERS.get(uid) or {
        "name": f"admin-{uid}",
        "role": "operator",
        "access": "global",
        "allowed_tags": "*",
        "greeting": "Отправь номер робота — покажу задачи.",
    }
    save_user(uid, seeded)
    try:
        from store.roles import add_full_admin

        add_full_admin(uid)
    except Exception:
        pass
    return seeded


def handle_start(bot_chat_id: int, user_id: int, profile: dict | None, reply_to: int | None, who: str) -> None:
    if is_admin_user(user_id):
        profile = _ensure_admin_profile(user_id, profile)
        kb = full_admin_reply_keyboard()
        body = help_text_for(user_id) + "\n\n<i>Кнопки внизу экрана ↑</i>"
        reply_with_daily_opener(
            bot_chat_id,
            user_id,
            profile,
            body,
            reply_to,
            who=who,
            parse_mode="HTML",
            reply_markup=kb,
        )
        _REPLY_KB_SENT.add(int(user_id))
        # Inline admin home so fresh install can manage without hunting ⚙️.
        reply_in_bot(
            bot_chat_id,
            user_id,
            admin_home_text(),
            parse_mode="HTML",
            reply_markup=admin_home_keyboard(),
        )
        return
    kb = reply_keyboard_for(user_id)
    if profile:
        body = help_text_for(user_id)
        if shows_bottom_reply_keyboard(kb):
            body += "\n\n<i>Кнопки внизу экрана ↑</i>"
        reply_with_daily_opener(
            bot_chat_id, user_id, profile, body, reply_to, who=who, parse_mode="HTML",
        )
        return
    if get_pending(user_id):
        reply_in_bot(bot_chat_id, user_id, ALREADY_PENDING_MSG, reply_to_message_id=reply_to)
        return
    start_registration(bot_chat_id, user_id, reply_to)


def handle_help(bot_chat_id: int, user_id: int, profile: dict | None, reply_to: int | None, who: str) -> None:
    if is_admin_user(user_id):
        profile = _ensure_admin_profile(user_id, profile)
    if profile:
        reply_with_daily_opener(
            bot_chat_id, user_id, profile, help_text_for(user_id), reply_to, who=who,
            parse_mode="HTML",
        )
    elif get_pending(user_id):
        reply_in_bot(bot_chat_id, user_id, ALREADY_PENDING_MSG, reply_to_message_id=reply_to)
    else:
        start_registration(bot_chat_id, user_id, reply_to)


_REPLY_KB_SENT: set[int] = set()


def ensure_reply_keyboard(user_id: int, *, force: bool = False) -> bool:
    """Install the bottom reply keyboard once per process (or force)."""
    uid = int(user_id)
    if not force and uid in _REPLY_KB_SENT:
        return False
    kb = reply_keyboard_for(uid)
    if not shows_bottom_reply_keyboard(kb):
        return False
    from dispatcher_roles import is_admin_user

    if is_admin_user(uid):
        text = "Кнопки внизу: ℹ️ Помощь · 🔍 Робот · ⚙️ Управление · 📊 Статус"
    else:
        text = "Кнопки внизу: ℹ️ Помощь · 🔍 Робот"
    if send_private(uid, text, reply_markup=kb):
        _REPLY_KB_SENT.add(uid)
        if len(_REPLY_KB_SENT) > USER_MAP_CAP * 2:
            _REPLY_KB_SENT.clear()
        return True
    return False


def handle_admin(admin_id: int, reply_to: int | None = None) -> None:
    from admin.sessions import clear_session

    clear_session(admin_id)
    # Reply keyboard and inline keyboard cannot share one message.
    # Do not force the «Кнопки внизу» hint on every tap — it interleaves with
    # background jobs (export ZIP). Recovery: /start pushes both bars.
    ensure_reply_keyboard(admin_id)
    reply_in_bot(
        admin_id,
        admin_id,
        admin_home_text(),
        parse_mode="HTML",
        reply_markup=admin_home_keyboard(),
    )
    log(f"admin {admin_id} → /admin")


def handle_quick_status(admin_id: int, reply_to: int | None) -> None:
    reply_in_bot(
        admin_id,
        admin_id,
        quick_status_text(),
        reply_to_message_id=reply_to,
        parse_mode="HTML",
        reply_markup=reply_keyboard_for(admin_id),
    )
    log(f"admin {admin_id} → статус (кнопка)")


def handle_pause(admin_id: int, reply_to: int | None) -> None:
    kb = reply_keyboard_for(admin_id)
    if is_paused():
        reply_in_bot(
            admin_id,
            admin_id,
            "Уже на паузе.\n\n" + admin_help_text(),
            reply_to_message_id=reply_to,
            reply_markup=kb,
        )
        log(f"admin {admin_id} → /pause (уже на паузе)")
        return
    set_paused(True)
    reply_in_bot(
        admin_id,
        admin_id,
        "⏸ Запросы робота для пользователей приостановлены.\n\n" + admin_help_text(),
        reply_to_message_id=reply_to,
        reply_markup=kb,
    )
    log(f"admin {admin_id} → /pause")


def handle_resume(admin_id: int, reply_to: int | None) -> None:
    kb = reply_keyboard_for(admin_id)
    if not is_paused():
        reply_in_bot(
            admin_id,
            admin_id,
            "Пауза не была включена.\n\n" + admin_help_text(),
            reply_to_message_id=reply_to,
            reply_markup=kb,
        )
        log(f"admin {admin_id} → /resume (паузы не было)")
        return
    set_paused(False)
    reply_in_bot(
        admin_id,
        admin_id,
        "▶️ Бот снова доступен для пользователей.\n\n" + admin_help_text(),
        reply_to_message_id=reply_to,
        reply_markup=kb,
    )
    log(f"admin {admin_id} → /resume")


def format_users_report() -> str:
    users = load_all_users()
    if not users:
        return "Пользователей в whitelist пока нет."
    lines = [f"<b>Пользователи ({len(users)})</b>", ""]
    for uid in sorted(users):
        profile = users[uid]
        name = escape(profile.get("name") or "?")
        role = escape(stored_role_label(profile))
        suffix = " · <b>admin</b>" if is_admin_user(uid) else ""
        lines.append(f"• {name} — {role}{suffix}\n  <code>{uid}</code>")
    return "\n".join(lines)


def handle_users(admin_id: int, reply_to: int | None) -> None:
    reply_in_bot(
        admin_id,
        admin_id,
        format_users_report(),
        reply_to_message_id=reply_to,
        parse_mode="HTML",
    )
    log(f"admin {admin_id} → /users")


def handle_stats(admin_id: int, text: str, reply_to: int | None) -> None:
    parts = text.strip().split()
    target_user_id: int | None = None
    if len(parts) > 1:
        try:
            target_user_id = int(parts[1])
        except ValueError:
            reply_in_bot(admin_id, admin_id, "Формат: /stats или /stats USER_ID", reply_to_message_id=reply_to)
            return
    report = format_stats_report(target_user_id=target_user_id)
    reply_in_bot(admin_id, admin_id, report, reply_to_message_id=reply_to, parse_mode="HTML")
    log(f"admin {admin_id} → /stats" + (f" {target_user_id}" if target_user_id else ""))


def handle_robot_query(
    bot_chat_id: int,
    user_id: int,
    profile: dict,
    rover: str,
    who: str,
    reply_to: int | None,
    *,
    tg_username: str | None = None,
) -> None:
    clear_robot_session(user_id)
    if reply_to:
        remember_user_robot_query_message(user_id, reply_to)

    search_body = f"⏳ Ищу задачи для <b>{escape(rover)}</b>…"
    loading_text, had_opener, opener = prepend_daily_opener(
        user_id,
        profile,
        search_body,
        for_robot_query=True,
    )
    loading_msg_id = send_private(
        user_id,
        loading_text,
        parse_mode="HTML",
        reply_to_message_id=reply_to,
        reply_markup=reply_keyboard_for(user_id),
    )
    if not loading_msg_id:
        send_private(user_id, TELEGRAM_UNAVAILABLE, reply_to_message_id=reply_to, retries=1)
        log(f"{who} ← {rover}: не удалось отправить подтверждение")
        return

    remember_user_view_message(user_id, loading_msg_id)
    if had_opener and opener:
        mark_greeted(user_id)
        log_outgoing(who, "приветствие", opener)

    record_robot_query(
        user_id,
        profile.get("name") or who.split(" (")[0],
        tg_username or profile.get("username"),
    )

    t0 = time.time()
    try:
        tasks, err = search_tasks_by_rover(rover)
    except Exception as e:
        tasks, err = [], safe_error(e)
    elapsed = time.time() - t0

    if err:
        text = TRACKER_UNAVAILABLE
        keyboard = None
        summary = f"ошибка Tracker — {err}"
    else:
        try:
            text, keyboard = build_robot_tasks_view(user_id, profile, rover, tasks)
        except Exception as e:
            text = TRACKER_UNAVAILABLE
            keyboard = None
            summary = f"ошибка формата — {safe_error(e)}"
        else:
            if not tasks:
                summary = "0 задач"
            elif not has_robot_access(user_id, profile, tasks):
                summary = "нет доступа по локации"
            else:
                visible = filter_tasks_for_user(user_id, profile, tasks)
                before_rules = len(visible)
                visible = filter_tasks_for_display(visible, rover)
                hidden = before_rules - len(visible)
                n = len(visible)
                if n == 0:
                    summary = f"0 задач (в Tracker {len(tasks)}, скрыто {hidden})" if hidden else "0 задач"
                elif hidden:
                    summary = f"{n} задач, скрыто {hidden}"
                else:
                    summary = f"{n} {'задача' if n == 1 else 'задачи' if 2 <= n <= 4 else 'задач'}"

    if _replace_loading_message(user_id, loading_msg_id, text, keyboard):
        preview = plain_preview(text, 80)
        log(f"{who} ← {rover}: {summary} ({elapsed:.1f}с) — {preview}")
    else:
        log(f"{who} ← {rover}: {summary} ({elapsed:.1f}с) — не доставлено")
    # Inline robot buttons cannot share a message with a ReplyKeyboard.
    # Operators/admins get the bottom bar on a follow-up; mechanics do not.
    ensure_reply_keyboard(user_id)


def process_message(msg: dict) -> None:
    if not is_bot_dialog(msg):
        return

    user = msg.get("from") or {}
    user_id = user["id"]
    bot_chat_id = user_id
    reply_to = msg.get("message_id")
    chat = msg.get("chat") or {}

    document = msg.get("document")
    if document:
        def _send(uid, body, reply_markup=None, parse_mode=None, **kwargs):
            send_private(uid, body, reply_markup=reply_markup, parse_mode=parse_mode)

        def _download(file_id: str, dest: Path) -> bool:
            try:
                r = _tg_session.get(f"{_api_url()}/getFile", params={"file_id": file_id}, timeout=60)
                r.raise_for_status()
                file_path = (r.json().get("result") or {}).get("file_path")
                if not file_path:
                    return False
                # rebuild URL with live token
                from store.secrets import get_telegram_bot_token
                url = f"https://api.telegram.org/file/bot{get_telegram_bot_token()}/{file_path}"
                fr = _tg_session.get(url, timeout=120)
                fr.raise_for_status()
                dest.write_bytes(fr.content)
                return True
            except Exception as e:
                log(f"download fail: {safe_error(e)}")
                return False

        if handle_admin_document(
            user_id=user_id,
            chat=chat,
            document=document,
            send=_send,
            download_file=_download,
        ):
            return
        return

    text = (msg.get("text") or "").strip()
    if not text:
        return

    profile = get_user_profile(user_id)
    who = user_label(user_id, profile, user)
    cmd = cmd_name(text)

    if is_admin_user(user_id):
        def _send(uid, body, reply_markup=None, parse_mode=None, **kwargs):
            send_private(uid, body, reply_markup=reply_markup, parse_mode=parse_mode)
        if handle_admin_text(user_id=user_id, text=text, send=_send):
            return

    if is_admin_user(user_id):
        pending_target = admin_awaiting_greeting(user_id)
        if pending_target is not None and not cmd.startswith("/") and not parse_robot_number(text):
            log(f"admin {user_id} → приветствие для {pending_target}")
            handle_admin_custom_greeting(user_id, pending_target, text, reply_to)
            return

    if user_registration_state(user_id) == AWAITING_NAME and not cmd.startswith("/"):
        log(f"{who} → имя: {text!r}")
        complete_registration(bot_chat_id, user_id, text, user, reply_to)
        return

    if cmd == "/start":
        log(f"{who} → /start")
        handle_start(bot_chat_id, user_id, profile, reply_to, who)
        return

    if cmd == "/help":
        log(f"{who} → /help")
        handle_help(bot_chat_id, user_id, profile, reply_to, who)
        return

    if cmd in ("/admin", "/pause", "/resume", "/stats", "/users"):
        if not is_admin_user(user_id):
            log(f"{who} → {cmd} · нет прав админа")
            return
        if cmd == "/admin":
            handle_admin(user_id, reply_to)
        elif cmd == "/pause":
            handle_pause(user_id, reply_to)
        elif cmd == "/resume":
            handle_resume(user_id, reply_to)
        elif cmd == "/users":
            handle_users(user_id, reply_to)
        else:
            handle_stats(user_id, text, reply_to)
        return

    if text == BTN_HELP:
        log(f"{who} → {BTN_HELP}")
        handle_help(bot_chat_id, user_id, profile, reply_to, who)
        return

    if text == BTN_ROBOT and profile:
        log(f"{who} → {BTN_ROBOT}")
        reply_with_daily_opener(
            bot_chat_id,
            user_id,
            profile,
            "Введите номер робота, например: <code>1842</code> или <code>a1842</code>",
            reply_to,
            who=who,
            parse_mode="HTML",
        )
        return

    if is_admin_user(user_id):
        if text == BTN_ADMIN:
            log(f"admin {user_id} → {BTN_ADMIN}")
            handle_admin(user_id, reply_to)
            return
        if text == BTN_STATUS:
            handle_quick_status(user_id, reply_to)
            return
        if text == BTN_PAUSE:
            handle_pause(user_id, reply_to)
            return
        if text == BTN_RESUME:
            handle_resume(user_id, reply_to)
            return

    rover = parse_robot_number(text)
    if not rover:
        if is_admin_user(user_id):
            profile = _ensure_admin_profile(user_id, profile)
        if profile:
            hint = "Отправьте номер робота, например: 1842 или a1842\n\n" + help_text_for(user_id)
            reply_with_daily_opener(bot_chat_id, user_id, profile, hint, reply_to, who=who)
            log(f"{who} → {text!r} · ← подсказка")
        elif get_pending(user_id):
            reply_in_bot(bot_chat_id, user_id, ALREADY_PENDING_MSG, reply_to_message_id=reply_to)
        else:
            start_registration(bot_chat_id, user_id, reply_to)
        return

    if not profile:
        log(f"{who} → {rover}")
        if get_pending(user_id):
            reply_in_bot(bot_chat_id, user_id, ALREADY_PENDING_MSG, reply_to_message_id=reply_to)
        else:
            start_registration(bot_chat_id, user_id, reply_to)
        return

    if is_paused() and not is_admin_user(user_id):
        log(f"{who} → {rover} · пауза")
        reply_with_daily_opener(
            bot_chat_id,
            user_id,
            profile,
            USER_PAUSED_MSG,
            reply_to,
            who=who,
            for_robot_query=True,
        )
        return

    log(f"{who} → {rover}")
    handle_robot_query(
        bot_chat_id, user_id, profile, rover, who, reply_to,
        tg_username=user.get("username"),
    )


def process_message_safe(msg: dict) -> None:
    try:
        process_message(msg)
    except Exception as e:
        log(f"Ошибка обработки: {safe_error(e)}")


def process_callback_safe(cb: dict) -> None:
    try:
        process_callback_query(cb)
    except Exception as e:
        log(f"Ошибка callback: {safe_error(e)}")


def load_update_offset() -> int:
    try:
        text = OFFSET_FILE.read_text().strip()
        if text.isdigit():
            return int(text)
    except FileNotFoundError:
        pass
    except OSError as e:
        log(f"✗ не прочитан offset: {safe_error(e)}")
    return 0


def save_update_offset(offset: int) -> None:
    try:
        OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_FILE.write_text(str(offset))
    except OSError as e:
        log(f"✗ не сохранён offset: {safe_error(e)}")


def _remove_pid_file() -> None:
    try:
        if PID_FILE.is_file() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


def acquire_single_instance() -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PID_FILE.is_file():
        try:
            old_pid = int(PID_FILE.read_text().strip())
        except (OSError, ValueError):
            old_pid = 0
        if old_pid and old_pid != os.getpid():
            try:
                os.kill(old_pid, 0)
            except OSError:
                pass
            else:
                log(f"✗ уже запущен (pid {old_pid})")
                sys.exit(1)
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(_remove_pid_file)


def main() -> None:
    pause_note = " · пауза для пользователей" if is_paused() else ""
    offset = load_update_offset()
    offset_note = f" · offset {offset}" if offset else ""
    log(
        f"🤖 Диспетчер-бот: long-poll {LONG_POLL_SEC}s"
        f"{pause_note}{offset_note}"
    )
    try:
        from store.clock_sync import clock_report, try_sync_moscow_time

        ok, detail = try_sync_moscow_time(force=True)
        rep = clock_report()
        log(
            f"MSK clock {rep.get('msk_now')}{rep.get('msk_offset')} "
            f"host_tz={rep.get('host_tz')} sync={'ok' if ok else detail}"
        )
    except Exception as e:
        log(f"MSK clock sync skip: {safe_error(e)}")
    try:
        if deliver_ota_result_notification(send_private):
            log("OTA result notification sent")
    except Exception as e:
        log(f"OTA notify skip: {safe_error(e)}")
    backoff = 0.5
    maint_cycles = 0

    while True:
        try:
            updates = get_updates(offset)
            backoff = 0.5
            for upd in updates:
                offset = upd["update_id"] + 1
                save_update_offset(offset)
                cb = upd.get("callback_query")
                if cb:
                    from_user = (cb.get("from") or {}).get("id")
                    if from_user and is_private_user(from_user, from_user):
                        threading.Thread(
                            target=process_callback_safe,
                            args=(cb,),
                            daemon=True,
                        ).start()
                    continue
                msg = upd.get("message")
                if not msg or not is_bot_dialog(msg):
                    continue
                threading.Thread(
                    target=process_message_safe,
                    args=(msg,),
                    daemon=True,
                ).start()
            maint_cycles += 1
            if maint_cycles % 8 == 0:
                try:
                    from admin.sessions import sweep_sessions
                    from store.housekeep import cap_mapping

                    sweep_sessions()
                    cap_mapping(_user_view_message_ids, USER_MAP_CAP)
                    cap_mapping(_user_robot_query_message_ids, USER_MAP_CAP)
                    if len(_REPLY_KB_SENT) > USER_MAP_CAP * 2:
                        _REPLY_KB_SENT.clear()
                except Exception as e:
                    log(f"maint sweep: {safe_error(e)}")
            if maint_cycles % 80 == 0:
                try:
                    from store.housekeep import maybe_run

                    maybe_run()
                except Exception as e:
                    log(f"housekeep: {safe_error(e)}")
            # Long-poll already blocked up to LONG_POLL_SEC; no extra sleep when idle.
        except Exception as e:
            log(f"✗ ошибка цикла: {safe_error(e)}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 8.0)


if __name__ == "__main__":
    try:
        acquire_single_instance()
        main()
    except KeyboardInterrupt:
        log("Остановлено.")
