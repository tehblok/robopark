#!/usr/bin/env python3
"""Мониторинг задач и отправка уведомлений в Telegram.

SLA по времени в очереди (рабочие часы 9–21 MSK). log_dump — в общем watchdog-блоке.
Логистика «Перемещение» — при LOGISTICS_ENABLED = True.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from store.secrets import safe_error

from config import DATA_DIR, LOGISTICS_ENABLED, TELEGRAM_CHATS, TELEGRAM_LOGISTICS_CHATS
from cleaner import is_log_dump_task
from queue_hours import parse_csv_utc, repair_hours_for_row


def _telegram_token() -> str:
    try:
        from store.secrets import get_telegram_bot_token

        return get_telegram_bot_token()
    except Exception:
        from credentials import TELEGRAM_BOT_TOKEN as token

        return token


def _api_url() -> str:
    return f"https://api.telegram.org/bot{(_telegram_token() or '').strip()}"


STATE_FILE = Path(DATA_DIR) / "watchdog_state.json"
CSV_FILE = Path(DATA_DIR) / "cleaned_report.csv"
SESSION = requests.Session()

LOG_DUMP_CATEGORY = "Слив логов"
LOG_DUMP_SECTION_TITLE = "Задача по сливу логов"
LOG_DUMP_FOOTER = (
    "Необходимо поставить робота на шнурок и проконтролировать, "
    "когда слив логов закончится"
)


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Состояние
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        with STATE_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_message(
    chat_id: int,
    thread_id: int | None,
    text: str,
    retries: int = 3,
    session: requests.Session | None = None,
) -> bool:
    http = session or SESSION
    for attempt in range(1, retries + 1):
        try:
            payload = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            }
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            resp = http.post(
                f"{_api_url()}/sendMessage",
                json=payload,
                timeout=45,
            )
            try:
                data = resp.json()
            except ValueError:
                data = {}
            if data.get("ok"):
                return True
            desc = data.get("description") or resp.text[:100]
            log(f"  ❌ Ошибка Telegram: {resp.status_code} {desc}")
            if attempt < retries:
                time.sleep(10)
        except requests.exceptions.Timeout:
            log(f"  ⏳ Таймаут (попытка {attempt}/{retries})")
            if attempt < retries:
                time.sleep(10)
        except requests.exceptions.RequestException as e:
            log(f"  ❌ Сетевая ошибка: {safe_error(e)}")
            if attempt < retries:
                time.sleep(10)
    log(f"  ❌ Не удалось отправить после {retries} попыток")
    return False


def get_chat(location: str) -> tuple[int, int] | None:
    try:
        from store.locations import chats

        return chats().get(location)
    except Exception:
        return TELEGRAM_CHATS.get(location)


def _service_chats() -> dict[str, tuple[int, int] | None]:
    try:
        from store.locations import chats, hourly_png_location_keys

        allowed = hourly_png_location_keys()
        return {k: v for k, v in chats().items() if k in allowed}
    except Exception:
        return {k: v for k, v in TELEGRAM_CHATS.items() if v is not None}


def _logistics_chats() -> dict[str, tuple[int, int]]:
    try:
        from store.locations import logistics_chats

        return logistics_chats()
    except Exception:
        return dict(TELEGRAM_LOGISTICS_CHATS)


# ---------------------------------------------------------------------------
# log_dump — категория в общем watchdog (без отдельных уведомлений)
# ---------------------------------------------------------------------------

def is_log_dump_row(row: pd.Series) -> bool:
    if str(row.get("Категория", "")).strip() == LOG_DUMP_CATEGORY:
        return True
    task = str(row.get("Задача", ""))
    return is_log_dump_task(task) and row.get("Статус") == "В очереди"


def prune_log_dump_state(df: pd.DataFrame, state: dict) -> None:
    """Убрать устаревшее состояние отдельных log_dump-уведомлений."""
    if "log_dump" in state:
        del state["log_dump"]


def _build_log_dump_message(tasks: list[dict]) -> str:
    lines = [f"🔌 {t['title'][:55]}" for t in tasks]
    return (
        f"<b>{LOG_DUMP_SECTION_TITLE}: {len(tasks)}</b>\n\n"
        + "\n".join(lines)
        + f"\n\n{LOG_DUMP_FOOTER}"
    )


# ---------------------------------------------------------------------------
# Правило: просроченные задачи
# ---------------------------------------------------------------------------

QUEUE_YELLOW_HOURS = 3  # ⚠️ 3–5 ч (включительно)
QUEUE_RED_HOURS = 5     # 🔴 > 5 ч
EXEMPT_STATUSES = {"Ждем смежников", "Ожидание поставки", "Закрыт", "Перемещение"}

SLA_FOOTER = "Необходимо взять в работу или сообщить причину задержки"
YELLOW_FOOTER = (
    "Приоритетная задача, необходимо взять в работу "
    "или сообщить о результатах диагностики"
)
GREEN_FOOTER = (
    "Необходимо взять в работу или сообщить о результатах диагностики"
)


def _queue_hours_series(df: pd.DataFrame, now: datetime | None = None) -> pd.Series:
    """Часы в очереди — как колонка «В ремонте» в таблице."""
    now_utc = pd.Timestamp.now(tz="UTC")
    if now is not None:
        now_utc = pd.Timestamp(now)
        if now_utc.tzinfo is None:
            now_utc = now_utc.tz_localize("UTC")
        else:
            now_utc = now_utc.tz_convert("UTC")

    return df.apply(lambda row: repair_hours_for_row(row, now_utc), axis=1)


def _sla_line(icon: str, title: str, hours: float) -> str:
    return f"{icon} {title[:55]} · {int(hours)}ч"


def _build_watchdog_message(
    urgent: list[dict],
    green: list[dict],
    log_dump: list[dict],
) -> str:
    parts: list[str] = []
    if log_dump:
        parts.append(_build_log_dump_message(log_dump))
    sla_part = _build_sla_message(urgent, green)
    if sla_part:
        parts.append(sla_part)
    return "\n\n".join(parts)


def _build_sla_message(urgent: list[dict], green: list[dict]) -> str:
    parts: list[str] = []

    red = [t for t in urgent if t["hours"] > QUEUE_RED_HOURS]
    yellow = [t for t in urgent if t["hours"] <= QUEUE_RED_HOURS]

    if red:
        red_lines = [
            _sla_line("🔴", t["title"], t["hours"])
            for t in sorted(red, key=lambda x: x["hours"], reverse=True)
        ]
        parts.append(
            f"<b>Превышено время в очереди: {len(red)}</b>\n\n"
            + "\n".join(red_lines)
            + f"\n\n{SLA_FOOTER}"
        )

    if yellow:
        yellow_lines = [
            _sla_line("⚠️", t["title"], t["hours"])
            for t in sorted(yellow, key=lambda x: x["hours"], reverse=True)
        ]
        parts.append(
            f"<b>Время отведенное на ремонт истекает: {len(yellow)}</b>\n\n"
            + "\n".join(yellow_lines)
            + f"\n\n{YELLOW_FOOTER}"
        )

    if green:
        green_lines = [
            _sla_line("🟢", t["title"], t["hours"])
            for t in sorted(green, key=lambda x: x["hours"], reverse=True)
        ]
        parts.append(
            f"<b>Новые задачи: {len(green)}</b>\n\n"
            + "\n".join(green_lines)
            + f"\n\n{GREEN_FOOTER}"
        )

    return "\n\n".join(parts)


def compute_sla_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    """Подготовить SLA-данные по локациям (один раз на прогон)."""
    work = df.copy()
    work["В_очереди_ч"] = _queue_hours_series(work)

    open_active = work[
        (work["Резолюция"] == "Открыт") &
        (work["Статус"] != "Закрыт")
    ]
    active_locations = set(open_active["Локация"].dropna())

    in_queue = open_active[open_active["Статус"] == "В очереди"]
    log_dump_rows = in_queue[in_queue.apply(is_log_dump_row, axis=1)]
    sla_queue = in_queue[~in_queue.apply(is_log_dump_row, axis=1)]

    by_location_urgent: dict[str, list[dict]] = {}
    by_location_green: dict[str, list[dict]] = {}
    by_location_log_dump: dict[str, list[dict]] = {}
    for _, row in sla_queue.iterrows():
        hours = row["В_очереди_ч"]
        loc = row.get("Локация", "")
        item = {"title": row["Задача"], "hours": hours}

        if hours >= QUEUE_YELLOW_HOURS:
            by_location_urgent.setdefault(loc, []).append(item)
        else:
            by_location_green.setdefault(loc, []).append(item)

    for _, row in log_dump_rows.iterrows():
        loc = row.get("Локация", "")
        by_location_log_dump.setdefault(loc, []).append({"title": row["Задача"]})

    watched = set(by_location_urgent) | set(by_location_green) | set(by_location_log_dump)
    for location in watched:
        if get_chat(location) is None:
            log(f"  ⚠️  Локация «{location}» не в TELEGRAM_CHATS, пропускаю")

    return {
        "active_locations": active_locations,
        "by_location_urgent": by_location_urgent,
        "by_location_green": by_location_green,
        "by_location_log_dump": by_location_log_dump,
    }


def send_deadline_for_location(
    location: str,
    chat: tuple[int, int],
    sla: dict[str, Any],
    session: requests.Session,
) -> None:
    """SLA-сообщение для одной локации."""
    chat_id, thread_id = chat
    active_locations = sla["active_locations"]
    urgent = sla["by_location_urgent"].get(location, [])
    green = sla["by_location_green"].get(location, [])
    log_dump = sla["by_location_log_dump"].get(location, [])

    if location not in active_locations:
        log(f"  ⏭️  {location}: активных задач нет, SLA-сообщение не отправляю")
        return

    if not urgent and not green and not log_dump:
        text = "Задачи с превышением времени в очереди — отсутствуют"
        if send_message(chat_id, thread_id, text, session=session):
            log(f"  📨 {location}: задач с превышением времени в очереди нет")
        return

    text = _build_watchdog_message(urgent, green, log_dump)
    if send_message(chat_id, thread_id, text, session=session):
        red = sum(1 for t in urgent if t["hours"] > QUEUE_RED_HOURS)
        yellow = len(urgent) - red
        detail = []
        if log_dump:
            detail.append(f"🔌 {len(log_dump)}")
        if urgent:
            detail.append(f"⚠️/🔴 {len(urgent)} (⚠️ {yellow}, 🔴 {red})")
        if green:
            detail.append(f"🟢 {len(green)}")
        total = len(urgent) + len(green) + len(log_dump)
        log(f"  📨 {location}: watchdog по {total} задачам ({', '.join(detail)})")


def run_service_watchdog_for_location(
    location: str,
    chat: tuple[int, int],
    df: pd.DataFrame,
    state: dict,
    sla: dict[str, Any],
    session: requests.Session,
    lock: threading.Lock,
) -> None:
    """SLA + log_dump для одной локации — строго после таблички."""
    send_deadline_for_location(location, chat, sla, session)


def rule_deadline(df: pd.DataFrame, state: dict) -> dict:
    """Сервисное SLA (последовательно, для ручного запуска watchdog.py)."""
    sla = compute_sla_snapshot(df)
    session = requests.Session()
    for location, chat in _service_chats().items():
        if chat is None:
            continue
        send_deadline_for_location(location, chat, sla, session)
    return state


# ---------------------------------------------------------------------------
# Правило: просроченное перемещение (логистика)
# ---------------------------------------------------------------------------

LOGISTICS_YELLOW_HOURS = 4   # 🟡 предупреждение
LOGISTICS_RED_HOURS = 20     # 🔴 критическая просрочка


def rule_logistics_movement(df: pd.DataFrame, state: dict) -> dict:
    """Задачи в статусе «Перемещение» дольше 4ч — уведомление в логистический чат.

    🟢 <4ч — без уведомления
    🟡 4–20ч — предупреждение
    🔴 >20ч — критическая просрочка
    """
    now_utc = pd.Timestamp.now(tz="UTC")

    df = df.copy()
    df["Создана"] = parse_csv_utc(df["Создана"])
    df["Возраст_ч"] = (now_utc - df["Создана"]).dt.total_seconds() / 3600

    candidates = df[
        (df["Статус"] == "Перемещение") &
        (df["Резолюция"] == "Открыт") &
        (df["Возраст_ч"] >= LOGISTICS_YELLOW_HOURS)
    ]

    by_location: dict[str, list[dict]] = {}
    for _, row in candidates.iterrows():
        loc = row.get("Локация", "")
        if loc not in by_location:
            by_location[loc] = []
        by_location[loc].append({
            "key": row["Ключ"],
            "title": row["Задача"],
            "hours": row["Возраст_ч"],
        })

    for location, chat in _logistics_chats().items():
        if chat is None:
            continue

        tasks = by_location.get(location, [])
        if not tasks:
            log(f"  ⏭️  {location}: просроченных перемещений нет")
            continue

        chat_id, thread_id = chat
        lines = []
        for t in sorted(tasks, key=lambda x: x["hours"], reverse=True):
            icon = "🔴" if t["hours"] >= LOGISTICS_RED_HOURS else "🟡"
            lines.append(f"{icon} {t['title'][:55]} · {int(t['hours'])}ч")

        text = (
            f"<b>Логистика: просроченное перемещение · {len(tasks)}</b>\n\n"
            + "\n".join(lines)
            + "\n\nПроверьте статус доставки объекта в сервис"
        )
        if send_message(chat_id, thread_id, text):
            log(f"  📨 {location}: логистика по {len(tasks)} задачам")

    return state


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def run_logistics_watchdog() -> None:
    """Только логистическое правило — для ручного тестирования."""
    if not CSV_FILE.exists():
        log(f"❌ Файл {CSV_FILE} не найден. Сначала запустите cleaner.py")
        return

    df = pd.read_csv(CSV_FILE)
    if df.empty:
        log("❌ CSV пустой, пропускаю")
        return

    state = load_state()
    log(f"📋 Логистика: {len(df)} задач в CSV")

    state = rule_logistics_movement(df, state)

    save_state(state)
    log("💾 Состояние сохранено")


def run_watchdog() -> None:
    if not CSV_FILE.exists():
        log(f"❌ Файл {CSV_FILE} не найден. Сначала запустите cleaner.py")
        return

    df = pd.read_csv(CSV_FILE)
    if df.empty:
        log("❌ CSV пустой, пропускаю")
        return

    state = load_state()
    log(f"📋 Загружено задач: {len(df)}, состояние: {sum(len(v) for v in state.values() if isinstance(v, dict))} записей")

    state = rule_deadline(df, state)
    if LOGISTICS_ENABLED:
        state = rule_logistics_movement(df, state)

    save_state(state)
    log("💾 Состояние сохранено")


if __name__ == "__main__":
    log("=== Watchdog старт ===")
    run_watchdog()
    log("=== Watchdog готово ===")
