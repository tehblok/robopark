#!/usr/bin/env python3
"""Полный цикл: сбор данных → генерация PNG → отправка в Telegram.

Для каждой локации строго по порядку: KillSwitch (если включено) → табличка → watchdog.
Локации обрабатываются параллельно и не блокируют друг друга.

Запуск вручную:
  python3 telegram_sender.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from store.secrets import safe_error

from config import DATA_DIR, KILLSWITCH_PROGRESS_ENABLED
from generate_killswitch_progress import (
    OUTPUT_DIR as KILLSWITCH_DIR,
    PROGRESS_LOCATIONS,
    generate_all_progress,
    progress_png_path,
)
from watchdog import (
    CSV_FILE,
    compute_sla_snapshot,
    load_state,
    prune_log_dump_state,
    run_service_watchdog_for_location,
    save_state,
)


def _telegram_token() -> str:
    try:
        from store.secrets import get_telegram_bot_token

        return get_telegram_bot_token()
    except Exception:
        from credentials import TELEGRAM_BOT_TOKEN as token

        return token


def _telegram_chats():
    try:
        from store.locations import chats

        return chats()
    except Exception:
        from config import TELEGRAM_CHATS

        return TELEGRAM_CHATS


TELEGRAM_BOT_TOKEN = _telegram_token()


def _api_url() -> str:
    token = (_telegram_token() or "").strip() or TELEGRAM_BOT_TOKEN
    return f"https://api.telegram.org/bot{token}"


TABLES_DIR = Path(DATA_DIR) / "location_tables"
PAUSE_FILE = Path(DATA_DIR) / ".send_paused"

SEND_RETRIES = 3
RETRY_PAUSE_SEC = 10


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run_pipeline() -> bool:
    log("1/3 Сбор данных из Tracker...")
    from report import collect

    if not collect():
        log("❌ Сбор из Tracker не удался — PNG не отправляю (ошибка выше)")
        return False

    csv_path = Path(DATA_DIR) / "latest_report.csv"
    if not csv_path.exists():
        log("❌ Файл latest_report.csv не найден — отправка отменена")
        return False
    df = pd.read_csv(csv_path)
    if df.empty:
        log("ℹ️  В Tracker нет blocker-задач — паркам уйдёт «все задачи выполнены»")

    log("2/3 Очистка и классификация...")
    from cleaner import run_cleaner
    run_cleaner()

    log("3/3 Генерация PNG-таблиц...")
    from grafika import generate_all_location_tables
    generate_all_location_tables()

    if KILLSWITCH_PROGRESS_ENABLED:
        log("4/4 KillSwitch progress PNG...")
        try:
            from store.locations import killswitch_location_keys

            only = sorted(killswitch_location_keys())
            generate_all_progress(KILLSWITCH_DIR, locations=only or None)
        except Exception as e:
            log(f"⚠️ KillSwitch PNG не сгенерированы: {safe_error(e)} — продолжаю без диаграмм")

    return True


def _thread_payload(thread_id: int | None) -> dict:
    if thread_id is None:
        return {}
    return {"message_thread_id": thread_id}


def send_photo(
    chat_id: int,
    thread_id: int | None,
    png_path: Path,
    session: requests.Session,
    label: str,
    *,
    kind: str = "фото",
) -> bool:
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            with png_path.open("rb") as f:
                response = session.post(
                    f"{_api_url()}/sendPhoto",
                    data={"chat_id": chat_id, **_thread_payload(thread_id)},
                    files={"photo": f},
                    timeout=60,
                )
            try:
                data = response.json()
            except ValueError:
                data = {}
            if data.get("ok"):
                log(f"✅ {label} → {kind} отправлено (thread {thread_id})")
                return True
            desc = data.get("description") or response.text[:120]
            log(f"❌ {label} → ошибка Telegram {response.status_code}: {desc}")
            if attempt < SEND_RETRIES:
                time.sleep(RETRY_PAUSE_SEC)
        except requests.exceptions.Timeout:
            log(f"⏳ {label} → таймаут (фото скорее всего дошло, retry пропускаем)")
            return True
        except requests.exceptions.RequestException as e:
            log(f"❌ {label} → сетевая ошибка таблички (попытка {attempt}/{SEND_RETRIES}): {safe_error(e)}")
            if attempt < SEND_RETRIES:
                time.sleep(RETRY_PAUSE_SEC)
    log(f"❌ {label} → табличка не отправлена после {SEND_RETRIES} попыток")
    return False


def send_message(
    chat_id: int,
    thread_id: int | None,
    text: str,
    session: requests.Session,
    label: str,
) -> bool:
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            response = session.post(
                f"{_api_url()}/sendMessage",
                json={
                    "chat_id": chat_id,
                    **_thread_payload(thread_id),
                    "text": text,
                },
                timeout=45,
            )
            try:
                data = response.json()
            except ValueError:
                data = {}
            if data.get("ok"):
                log(f"✅ {label} → сообщение отправлено (thread {thread_id})")
                return True
            desc = data.get("description") or response.text[:120]
            log(f"❌ {label} → ошибка Telegram {response.status_code}: {desc}")
            if attempt < SEND_RETRIES:
                time.sleep(RETRY_PAUSE_SEC)
        except requests.exceptions.Timeout:
            log(f"⏳ {label} → таймаут сообщения (попытка {attempt}/{SEND_RETRIES})")
        except requests.exceptions.RequestException as e:
            log(f"❌ {label} → сетевая ошибка сообщения: {safe_error(e)}")
        if attempt < SEND_RETRIES:
            time.sleep(RETRY_PAUSE_SEC)
    log(f"❌ {label} → сообщение не отправлено после {SEND_RETRIES} попыток")
    return False


def send_table_for_location(
    location: str,
    chat: tuple[int, int],
    session: requests.Session,
) -> bool:
    chat_id, thread_id = chat
    safe_name = location.replace("/", "-")
    png_path = TABLES_DIR / f"{safe_name}.png"

    if not png_path.exists():
        log(f"ℹ️  {location} → активных задач нет, отправляю сообщение")
        return send_message(
            chat_id,
            thread_id,
            "На текущий момент все задачи выполнены",
            session,
            location,
        )

    return send_photo(chat_id, thread_id, png_path, session, location, kind="табличка")


def send_killswitch_for_location(
    location: str,
    chat: tuple[int, int],
    session: requests.Session,
) -> bool:
    try:
        from store.locations import killswitch_location_keys

        if location not in killswitch_location_keys():
            return True
    except Exception:
        if location not in PROGRESS_LOCATIONS:
            return True

    png_path = progress_png_path(location, KILLSWITCH_DIR)
    if not png_path.exists():
        log(f"⏭️  {location} → KillSwitch PNG нет, пропускаю")
        return False

    chat_id, thread_id = chat
    return send_photo(
        chat_id,
        thread_id,
        png_path,
        session,
        location,
        kind="KillSwitch",
    )


def deliver_location(
    location: str,
    chat: tuple[int, int] | None,
    df: pd.DataFrame,
    state: dict,
    sla: dict,
    lock: threading.Lock,
) -> bool:
    """Табличка → watchdog для одной локации."""
    if chat is None:
        log(f"⏭️  {location} → chat_id/thread_id ещё не настроены, пропускаю")
        return False

    session = requests.Session()
    try:
        if KILLSWITCH_PROGRESS_ENABLED:
            send_killswitch_for_location(location, chat, session)
        table_ok = send_table_for_location(location, chat, session)

        log(f"--- Watchdog {location} ---")
        run_service_watchdog_for_location(location, chat, df, state, sla, session, lock)
        return table_ok
    finally:
        session.close()


def _hourly_delivery_targets() -> list[tuple[str, tuple[int, int] | None]]:
    try:
        from store.locations import chats, hourly_png_location_keys

        allowed = hourly_png_location_keys()
        return [(key, chat) for key, chat in chats().items() if key in allowed]
    except Exception:
        return list(_telegram_chats().items())


def deliver_all() -> tuple[int, int]:
    if CSV_FILE.exists():
        df = pd.read_csv(CSV_FILE)
    else:
        df = pd.DataFrame()
    if df.empty:
        log("ℹ️  cleaned_report пустой — отправляю в парки «все задачи выполнены»")

    state = load_state()
    log(
        f"📋 Загружено задач: {len(df)}, "
        f"состояние: {sum(len(v) for v in state.values() if isinstance(v, dict))} записей"
    )
    prune_log_dump_state(df, state)
    sla = compute_sla_snapshot(df)
    lock = threading.Lock()

    locations = _hourly_delivery_targets()
    if not locations:
        log("📨 Итого: отправлено 0, пропущено 0 (нет локаций с hourly_png)")
        return 0, 0

    sent = 0
    skipped = 0
    max_workers = min(len(locations), 8)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(deliver_location, location, chat, df, state, sla, lock): location
            for location, chat in locations
        }
        for future in as_completed(futures):
            if future.result():
                sent += 1
            else:
                skipped += 1

    save_state(state)
    log("💾 Состояние watchdog сохранено")
    log(f"📨 Итого: табличек/сообщений отправлено {sent}, пропущено {skipped}")
    return sent, skipped


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_send_lock(lock: Path) -> bool:
    """True = этот процесс шлёт. False = уже живой отправитель."""
    if lock.is_file():
        try:
            old = int((lock.read_text(encoding="utf-8") or "0").strip() or "0")
        except (OSError, ValueError):
            old = 0
        if _pid_alive(old):
            log(f"⏸️  Уже идёт рассылка (pid {old}) — выход")
            return False
        log("⚠️  Старый send lock (процесс мёртв) — продолжаю")
    try:
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    return True


def _run_housekeep() -> None:
    try:
        from store.housekeep import maybe_run

        result = maybe_run()
        if result.get("ran") and result.get("removed"):
            log(f"🧹 housekeep: {result.get('summary', '')}")
    except Exception as e:
        log(f"⚠️ housekeep: {safe_error(e)}")


def main() -> None:
    force = "--force" in sys.argv[1:]
    if PAUSE_FILE.exists() and not force:
        log("⏸️  Отправка приостановлена (data/.send_paused) — выход")
        log("    (принудительно: telegram_sender.py --force)")
        _run_housekeep()
        return
    if force and PAUSE_FILE.exists():
        log("⚠️  Send pause активен — принудительный прогон (--force)")
    if not force:
        try:
            from store.schedules import in_send_window

            if not in_send_window():
                log("⏭️  Сейчас вне окна PNG из /admin → Расписание — выход")
                log("    (принудительно: telegram_sender.py --force)")
                _run_housekeep()
                return
        except Exception as e:
            log(f"⚠️  Не прочитал окно PNG-рассылки: {safe_error(e)} — не отправляю")
            _run_housekeep()
            return

    lock = Path(DATA_DIR) / ".send_running"
    if not _acquire_send_lock(lock):
        _run_housekeep()
        sys.exit(2)

    log("=== Старт ===")
    try:
        ok = run_pipeline()
        if not ok:
            sys.exit(1)
        sent, skipped = deliver_all()
        log("=== Готово ===")
        if sent == 0:
            log("❌ Ни в один парк ничего не ушло")
            sys.exit(1)
    except Exception as e:
        log(f"❌ Ошибка пайплайна: {safe_error(e)}")
        sys.exit(1)
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass
        _run_housekeep()


if __name__ == "__main__":
    main()
