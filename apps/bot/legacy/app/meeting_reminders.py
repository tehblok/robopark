"""
Напоминания о встречах в Zoom для локаций.

Отдельный самостоятельный скрипт — не связан с telegram_sender.py / watchdog.py,
чтобы не влиять на основную рассылку отчётов и вatчдогов.

Использование:
    python meeting_reminders.py <ключ_напоминания>
    python meeting_reminders.py tick              # проверить расписание (cron/systemd)
    python meeting_reminders.py schedule [дней]   # расписание групп A/B

Ключ напоминания — один из ключей REMINDERS или daily_slot_1 … daily_slot_4.
Время отправки — data/schedules.json (админка /admin → Расписание).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from store.secrets import safe_error

from store.broadcasts import (
    END_OF_DAY_TEXT,
    LIDAR_STICKER_TEXT,
    MOSCOW_PARK_KEYS,
    ROBOT_CHARGE_TEXT,
)
from store.schedules import (
    JOB_RETRY_MINUTES,
    due_jobs,
    force_moscow_timezone,
    job_by_id,
    load_schedules,
    mark_fired,
    mark_location_sent,
    minutes_since_fire,
    planner_group_for_date,
    schedule_timezone,
    was_location_sent,
)
from store.zoom_jobs import (
    KIND_PLANNER_AB,
    KIND_SIMPLE,
    DAILY_TEXT,
    WEEKLY_TEXT,
)


def _telegram_token() -> str:
    try:
        from store.secrets import get_telegram_bot_token

        return get_telegram_bot_token()
    except Exception:
        from credentials import TELEGRAM_BOT_TOKEN as token

        return token


def _api_url() -> str:
    return f"https://api.telegram.org/bot{_telegram_token()}"


def _location_chats() -> dict[str, tuple[int, int] | None]:
    from store.locations import chats

    return chats()


# 7 августа 2026 — первый день группы A
PLANNER_ANCHOR = date(2026, 8, 7)

MOSCOW_PARKS = list(MOSCOW_PARK_KEYS)
WEEKDAY_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

# Kept for format_schedule / tests that still reference slot shape.
DAILY_PLANNER_SLOTS: dict[str, dict[str, dict]] = {}
REMINDERS: dict[str, dict] = {}


def _planner_anchor() -> date:
    raw = load_schedules().get("planner_anchor") or "2026-08-07"
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return PLANNER_ANCHOR


def planner_group(on_date: date | None = None) -> str:
    return planner_group_for_date(on_date)

def _refresh_legacy_maps() -> None:
    """Populate module maps from schedules.json for old helpers."""
    global DAILY_PLANNER_SLOTS, REMINDERS
    slots: dict[str, dict[str, dict]] = {}
    simple: dict[str, dict] = {}
    for job in load_schedules()["jobs"]:
        jid = str(job.get("id") or "")
        if not jid:
            continue
        if (job.get("kind") or KIND_SIMPLE) == KIND_PLANNER_AB:
            groups = job.get("groups") or {}
            slots[jid] = {
                "meeting_time": str(job.get("fire_at") or ""),
                "A": dict(groups.get("A") or {}),
                "B": dict(groups.get("B") or {}),
            }
        else:
            simple[jid] = {
                "locations": list(job.get("locations") or []),
                "text": job.get("text") or WEEKLY_TEXT,
                "link": job.get("link") or "",
            }
    DAILY_PLANNER_SLOTS = slots
    REMINDERS = simple


def resolve_reminder(key: str, on_date: date | None = None) -> dict | None:
    job = job_by_id(key)
    if not job or not job.get("enabled", True):
        # Fall back to legacy maps once
        if not DAILY_PLANNER_SLOTS and not REMINDERS:
            _refresh_legacy_maps()
        slot = DAILY_PLANNER_SLOTS.get(key)
        if slot:
            group = planner_group(on_date)
            entry = slot[group]
            return {
                "locations": list(entry.get("locations") or []),
                "text": DAILY_TEXT,
                "link": entry.get("link") or "",
                "group": group,
                "label": entry.get("label") or "",
            }
        reminder = REMINDERS.get(key)
        if not reminder:
            return None
        out = dict(reminder)
        if callable(out.get("locations")):
            out["locations"] = out["locations"]()
        return out

    kind = job.get("kind") or KIND_SIMPLE
    text = str(job.get("text") or DAILY_TEXT)
    alt = job.get("alternate")
    if alt in ("A", "B"):
        group = planner_group(on_date)
        if group != alt:
            return None
    if kind == KIND_PLANNER_AB:
        group = planner_group(on_date)
        groups = job.get("groups") or {}
        entry = groups.get(group) or {}
        return {
            "locations": list(entry.get("locations") or []),
            "text": text,
            "link": entry.get("link") or "",
            "group": group,
            "label": entry.get("label") or "",
        }
    out = {
        "locations": list(job.get("locations") or []),
        "text": text,
        "link": job.get("link") or "",
    }
    if alt in ("A", "B"):
        out["group"] = alt
    return out


def format_schedule(days: int = 31, start: date | None = None) -> str:
    jobs = [
        j
        for j in load_schedules()["jobs"]
        if (j.get("kind") or KIND_SIMPLE) == KIND_SIMPLE
        and j.get("alternate") in ("A", "B")
        and j.get("enabled", True)
    ]
    anchor = _planner_anchor()
    start = start or anchor
    lines = [
        f"Утренние Zoom (один парк = один слот, A/B через день, якорь {anchor.isoformat()})",
        "",
    ]
    for job in sorted(jobs, key=lambda j: (j.get("fire_at") or "", j.get("id") or "")):
        locs = ", ".join(job.get("locations") or []) or "?"
        lines.append(
            f"{job.get('id')}: {locs} · {job.get('alternate')} @ {job.get('fire_at')}"
        )
    lines.extend(["", f"{'Дата':<12} {'День':<4} {'Группа':<6} Парки", "-" * 72])
    for offset in range(days):
        d = start + timedelta(days=offset)
        group = planner_group(d)
        labels = []
        for job in sorted(jobs, key=lambda j: j.get("fire_at") or ""):
            if job.get("alternate") != group:
                continue
            locs = job.get("locations") or []
            labels.append(str(locs[0] if locs else job.get("label") or "?"))
        lines.append(
            f"{d.strftime('%d.%m.%Y'):<12} {WEEKDAY_RU[d.weekday()]:<4} {group:<6} "
            f"{' · '.join(labels) or '—'}"
        )
    return "\n".join(lines)


def _post_telegram_message(payload: dict) -> tuple[bool, str]:
    last = ""
    for attempt in range(1, 4):
        try:
            response = requests.post(
                f"{_api_url()}/sendMessage",
                json=payload,
                timeout=30,
            )
            if response.ok:
                return True, ""
            last = f"{response.status_code}: {response.text[:200]}"
            if response.status_code == 429:
                wait = 2 * attempt
                try:
                    wait = int((response.json() or {}).get("parameters", {}).get("retry_after") or wait)
                except Exception:
                    pass
                time.sleep(max(0, min(wait, 30)))
                continue
            if response.status_code >= 500 and attempt < 3:
                time.sleep(1)
                continue
            return False, last
        except requests.exceptions.RequestException as e:
            last = safe_error(e)
            if attempt < 3:
                time.sleep(1)
    return False, last


def send_reminder(key: str, on_date: date | None = None, *, mark_state: bool = True) -> bool:
    reminder = resolve_reminder(key, on_date)
    if not reminder:
        print(f"❌ Неизвестный ключ напоминания: {key}")
        return False

    message = reminder["text"].format(link=reminder.get("link", ""))
    group_note = f", группа {reminder['group']}" if "group" in reminder else ""
    chat_map = _location_chats()
    tz = schedule_timezone(load_schedules().get("timezone"))
    now = datetime.now(tz)
    today = on_date or now.date()
    job = job_by_id(key)
    if job and mark_state and today == now.date():
        delta = minutes_since_fire(job, now)
        if delta is not None and delta > JOB_RETRY_MINUTES:
            print(
                f"⏭️  {key}: слишком поздно для отправки "
                f"(сейчас {now.isoformat()}, fire_at={job.get('fire_at')}, +{delta} мин)"
            )
            return False
        if delta is not None and delta >= 2:
            print(
                f"⚠️  {key}: отправка с опозданием +{delta} мин "
                f"(сейчас {now.isoformat()}, fire_at={job.get('fire_at')})"
            )
    sent = 0
    failed = 0

    for location in reminder["locations"]:
        chat = chat_map.get(location)
        if not chat:
            print(
                f"⚠️ Для локации {location} не настроен чат "
                f"(key in chats={location in chat_map}, "
                f"pair={chat_map.get(location)!r}), пропускаем."
            )
            failed += 1
            continue
        if mark_state and was_location_sent(key, location, today):
            sent += 1
            continue
        chat_id, thread_id = chat
        if thread_id is None:
            print(
                f"⚠️ {location}: chat_id={chat_id} без thread_id "
                f"(forum-тема нужна) — отправка может не попасть в топик"
            )
        payload: dict = {"chat_id": chat_id, "text": message}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        ok, err = _post_telegram_message(payload)
        if ok:
            sent += 1
            if mark_state:
                mark_location_sent(key, location, today)
            print(
                f"✅ {location} ({key}{group_note}) → напоминание отправлено "
                f"@ {datetime.now(tz).isoformat()}"
            )
        else:
            failed += 1
            print(f"❌ {location} ({key}{group_note}) → ошибка {err}")

    if mark_state and failed == 0 and sent > 0:
        mark_fired(key, today)
    return sent > 0


LOCK_STALE_SEC = 120


def run_tick() -> int:
    """Fire due reminders for current minute; return count sent."""
    from paths import DATA_DIR

    lock = Path(DATA_DIR) / ".reminders_running"
    if lock.is_file():
        try:
            old = int((lock.read_text(encoding="utf-8") or "0").strip() or "0")
        except (OSError, ValueError):
            old = 0
        try:
            age = time.time() - lock.stat().st_mtime
        except OSError:
            age = LOCK_STALE_SEC + 1
        alive = False
        if old > 0:
            try:
                os.kill(old, 0)
            except OSError:
                alive = False
            else:
                alive = True
        if alive:
            if age > LOCK_STALE_SEC:
                print(
                    f"⏭️  tick still running (pid {old}, {int(age)}s) — "
                    "второй тик не стартую"
                )
            else:
                print(f"⏭️  tick already running (pid {old})")
            return 0
        print(f"⚠️  reminders lock без живого процесса (pid {old}) — снимаю")
    try:
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    try:
        return _run_tick_body()
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def _run_tick_body() -> int:
    force_moscow_timezone()
    try:
        from store.clock_sync import clock_report, try_sync_moscow_time

        ok, detail = try_sync_moscow_time(force=False)
        report = clock_report()
        if ok:
            print(f"clock sync MSK ok host_tz={report.get('host_tz')}")
        elif detail not in ("cooldown",):
            print(f"⚠️ clock sync MSK: {detail}")
        print(
            f"tick wall MSK={report.get('msk_now')}{report.get('msk_offset')} "
            f"host_tz={report.get('host_tz')} env_TZ={report.get('env_tz') or '—'}"
        )
    except Exception as e:
        print(f"⚠️ clock sync: {safe_error(e)}")
    schedules = load_schedules()
    tz = schedule_timezone(schedules.get("timezone"))
    now = datetime.now(tz)
    today = now.date()
    print(f"tick clock {now.isoformat()} tz={tz}")
    fired = 0
    for job_id in due_jobs(now):
        ok = send_reminder(job_id, today, mark_state=True)
        if ok:
            fired += 1
        else:
            print(f"⚠️ tick: {job_id} — nothing sent")
    try:
        from store.broadcasts import run_broadcast_tick

        bf = run_broadcast_tick()
        if bf:
            print(f"tick {now.strftime('%H:%M')} → sent {bf} broadcast(s)")
        fired += bf
    except Exception as e:
        print(f"⚠️ broadcast tick error: {safe_error(e)}")
    try:
        from store.sk_campaigns import run_sk_tick

        sf = run_sk_tick()
        if sf:
            print(f"tick {now.strftime('%H:%M')} → sent {sf} sk campaign(s)")
        fired += sf
    except Exception as e:
        print(f"⚠️ sk tick error: {safe_error(e)}")
    if fired:
        print(f"tick {now.strftime('%H:%M')} → total sent {fired}")
    try:
        from store.housekeep import maybe_run

        maybe_run()
    except Exception as e:
        print(f"⚠️ housekeep: {safe_error(e)}")
    return fired


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python meeting_reminders.py <ключ_напоминания>")
        print("               python meeting_reminders.py tick")
        print("               python meeting_reminders.py schedule [дней]")
        sys.exit(1)

    if sys.argv[1] == "schedule":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 31
        print(format_schedule(days=days))
        sys.exit(0)

    if sys.argv[1] == "tick":
        run_tick()
        sys.exit(0)

    ok = send_reminder(sys.argv[1], mark_state=False)
    sys.exit(0 if ok else 1)
