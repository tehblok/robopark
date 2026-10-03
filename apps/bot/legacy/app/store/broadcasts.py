"""Scheduled custom text broadcasts to location chats (data/broadcasts.json)."""

from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from store.secrets import safe_error

from store import DATA_DIR
from store.schedules import DEFAULT_TIMEZONE, JOB_RETRY_MINUTES, _normalize_fire_at, minutes_since_fire

BROADCASTS_FILE = DATA_DIR / "broadcasts.json"
BROADCAST_STATE_FILE = DATA_DIR / "broadcast_state.json"

REPEAT_ONCE = "once"
REPEAT_DAILY = "daily"
REPEAT_WEEKDAYS = "weekdays"

REPEAT_LABELS = {
    REPEAT_ONCE: "один раз",
    REPEAT_DAILY: "каждый день",
    REPEAT_WEEKDAYS: "пн–пт",
}

LOC_ALL = "all"
LOC_HOURLY = "hourly_png"
LOC_KEYS = "keys"

# Same park texts as meeting_reminders used to send on a schedule.
ROBOT_CHARGE_TEXT = (
    "🔋 Напоминаю, все роботы на которых завершены ремонтные работы, должны быть "
    "с полностью заряженными АКБ и включены, выключенного робота не смогут вывести в прод!"
    "\n\n──────────────\n\n"
    "🏷️ При установке или выходе из строя новых рессор с наклейкой, сообщите об этом в чат "
    "и в отчетах прописывайте явно — «Рессора с наклейкой»."
)

END_OF_DAY_TEXT = "Всем спасибо! Все молодцы! Отлично поработали сегодня!"

LIDAR_STICKER_TEXT = (
    "\u26a0\ufe0f Внимание! Наклейка с номером на башне лидара должна быть на каждом роботе.\n\n"
    "При любом заезде в гараж — блокер, перераспределение, хранение — сразу "
    "проверяйте и при необходимости наклеивайте. В отчёте указывайте как "
    "выявленный дефект, в обычном формате."
)

MOSCOW_PARK_KEYS = ["Next", "Север (Москва)", "Сигма", "Континент", "АрмаМСК"]

# Old schedules.json job ids — tick must not send these (now broadcasts).
LEGACY_TEXT_JOB_IDS = frozenset(
    {"robot_charge_daily", "end_of_day_daily", "lidar_sticker_daily"}
)

BUILTIN_SPECS: list[dict[str, Any]] = [
    {
        "id": "builtin_robot_charge",
        "label": "Заряд АКБ и рессоры",
        "text": ROBOT_CHARGE_TEXT,
        "fire_at": "08:00",
        "repeat": REPEAT_WEEKDAYS,
        "location_mode": LOC_ALL,
    },
    {
        "id": "builtin_end_of_day",
        "label": "Конец дня",
        "text": END_OF_DAY_TEXT,
        "fire_at": "21:00",
        "repeat": REPEAT_WEEKDAYS,
        "location_mode": LOC_ALL,
    },
    {
        "id": "builtin_lidar_sticker",
        "label": "Наклейка лидара",
        "text": LIDAR_STICKER_TEXT,
        "fire_at": "15:38",
        "repeat": REPEAT_DAILY,
        "location_mode": LOC_KEYS,
        "location_keys": list(MOSCOW_PARK_KEYS),
    },
]

BUILTIN_IDS = frozenset(spec["id"] for spec in BUILTIN_SPECS)

_lock = threading.RLock()


def _default_payload() -> dict[str, Any]:
    return {"version": 1, "campaigns": [], "removed_ids": []}


def _new_builtin_row(spec: dict[str, Any]) -> dict[str, Any]:
    row = {
        "id": spec["id"],
        "label": spec["label"],
        "text": spec["text"],
        "enabled": False,
        "fire_at": spec["fire_at"],
        "repeat": spec["repeat"],
        "builtin": True,
        "location_mode": spec.get("location_mode") or LOC_ALL,
        "created_at": "seed",
        "created_by": None,
    }
    if spec.get("location_keys"):
        row["location_keys"] = list(spec["location_keys"])
    return row


def _load_state() -> dict[str, str]:
    if not BROADCAST_STATE_FILE.is_file():
        return {}
    try:
        raw = json.loads(BROADCAST_STATE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    BROADCAST_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BROADCAST_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _seed_builtins(data: dict[str, Any]) -> bool:
    removed = {str(x) for x in (data.get("removed_ids") or [])}
    campaigns = list(data.get("campaigns") or [])
    by_id = {str(c.get("id")): c for c in campaigns if c.get("id")}
    changed = False
    incoming: list[dict[str, Any]] = []
    for spec in BUILTIN_SPECS:
        cid = spec["id"]
        if cid in removed:
            continue
        if cid in by_id:
            row = by_id[cid]
            if row.get("text") != spec["text"] or row.get("label") != spec["label"]:
                row["text"] = spec["text"]
                row["label"] = spec["label"]
                row["builtin"] = True
                changed = True
            continue
        incoming.append(_new_builtin_row(spec))
        changed = True
    if incoming:
        campaigns = incoming + campaigns
    data["campaigns"] = campaigns
    if "removed_ids" not in data or not isinstance(data.get("removed_ids"), list):
        data["removed_ids"] = list(removed)
        changed = True
    return changed


def load_broadcasts(*, force_reload: bool = False) -> dict[str, Any]:
    del force_reload
    with _lock:
        data = _default_payload()
        if BROADCASTS_FILE.is_file():
            try:
                raw = json.loads(BROADCASTS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("campaigns"), list):
                    data["campaigns"] = list(raw["campaigns"])
                    data["removed_ids"] = list(raw.get("removed_ids") or [])
            except (OSError, json.JSONDecodeError):
                pass
        if _seed_builtins(data):
            _write_unlocked(data)
        return deepcopy(data)


def _write_unlocked(data: dict[str, Any]) -> None:
    BROADCASTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "campaigns": list(data.get("campaigns") or []),
        "removed_ids": [str(x) for x in (data.get("removed_ids") or [])],
    }
    tmp = BROADCASTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(BROADCASTS_FILE)


def save_broadcasts(data: dict[str, Any]) -> None:
    with _lock:
        _write_unlocked(data)


def campaign_by_id(campaign_id: str) -> dict[str, Any] | None:
    for row in load_broadcasts()["campaigns"]:
        if row.get("id") == campaign_id:
            return row
    return None


def list_campaigns(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    rows = load_broadcasts()["campaigns"]
    if enabled_only:
        return [r for r in rows if r.get("enabled")]
    builtins = [r for r in rows if r.get("builtin")]
    custom = [r for r in rows if not r.get("builtin")]
    return builtins + custom


def removed_builtin_ids() -> list[str]:
    return [str(x) for x in load_broadcasts().get("removed_ids") or []]


def add_campaign(
    *,
    text: str,
    fire_at: str,
    repeat: str,
    created_by: int | None = None,
) -> dict[str, Any]:
    body = text.strip()
    if not body:
        raise ValueError("пустой текст")
    if len(body) > 4000:
        raise ValueError("текст длиннее 4000 символов")
    repeat_norm = repeat if repeat in REPEAT_LABELS else REPEAT_ONCE
    fire_norm = _normalize_fire_at(fire_at)
    row = {
        "id": f"bcast_{uuid.uuid4().hex[:10]}",
        "text": body,
        "enabled": True,
        "fire_at": fire_norm,
        "repeat": repeat_norm,
        "builtin": False,
        "location_mode": LOC_HOURLY,
        "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": int(created_by) if created_by is not None else None,
    }
    data = load_broadcasts()
    data["campaigns"].append(row)
    save_broadcasts(data)
    return row


def set_campaign_enabled(campaign_id: str, enabled: bool) -> bool:
    data = load_broadcasts()
    found = False
    for row in data["campaigns"]:
        if row.get("id") == campaign_id:
            row["enabled"] = bool(enabled)
            found = True
            break
    if found:
        save_broadcasts(data)
    return found


def disable_all_campaigns() -> int:
    data = load_broadcasts()
    n = 0
    for row in data["campaigns"]:
        if row.get("enabled"):
            row["enabled"] = False
            n += 1
    if n:
        save_broadcasts(data)
    return n


def delete_campaign(campaign_id: str) -> bool:
    data = load_broadcasts()
    before = len(data["campaigns"])
    removed_row = next((r for r in data["campaigns"] if r.get("id") == campaign_id), None)
    data["campaigns"] = [r for r in data["campaigns"] if r.get("id") != campaign_id]
    if len(data["campaigns"]) >= before:
        return False
    if removed_row and (removed_row.get("builtin") or campaign_id in BUILTIN_IDS):
        rid = list(data.get("removed_ids") or [])
        if campaign_id not in rid:
            rid.append(campaign_id)
        data["removed_ids"] = rid
    save_broadcasts(data)
    return True


def restore_removed_builtins() -> int:
    data = load_broadcasts()
    n = len(data.get("removed_ids") or [])
    data["removed_ids"] = []
    save_broadcasts(data)
    load_broadcasts()
    return n


def was_fired(campaign_id: str, on_date: date) -> bool:
    key = f"{campaign_id}:{on_date.isoformat()}"
    return _load_state().get(key) == "1"


def was_location_sent(campaign_id: str, location: str, on_date: date) -> bool:
    key = f"{campaign_id}:{on_date.isoformat()}:loc:{location}"
    return _load_state().get(key) == "1"


def _prune_state(state: dict[str, str], on_date: date) -> None:
    cutoff = on_date.toordinal() - 14
    for k in list(state):
        parts = k.split(":")
        if len(parts) < 2:
            continue
        try:
            d = date.fromisoformat(parts[1])
        except ValueError:
            continue
        if d.toordinal() < cutoff:
            del state[k]


def mark_location_sent(campaign_id: str, location: str, on_date: date) -> None:
    with _lock:
        state = _load_state()
        state[f"{campaign_id}:{on_date.isoformat()}:loc:{location}"] = "1"
        _prune_state(state, on_date)
        _save_state(state)


def mark_fired(campaign_id: str, on_date: date) -> None:
    with _lock:
        state = _load_state()
        state[f"{campaign_id}:{on_date.isoformat()}"] = "1"
        _prune_state(state, on_date)
        _save_state(state)


def _campaign_matches_now(campaign: dict[str, Any], now: datetime) -> bool:
    if not campaign.get("enabled"):
        return False
    fire_at = campaign.get("fire_at")
    if not fire_at:
        return False
    delta = minutes_since_fire({"fire_at": fire_at}, now)
    if delta is None or not (0 <= delta <= JOB_RETRY_MINUTES):
        return False
    repeat = campaign.get("repeat") or REPEAT_ONCE
    if repeat == REPEAT_WEEKDAYS and now.weekday() > 4:
        return False
    return True


def due_campaign_ids(now: datetime | None = None) -> list[str]:
    try:
        from store.schedules import load_schedules

        tz_name = load_schedules().get("timezone") or DEFAULT_TIMEZONE
    except Exception:
        tz_name = DEFAULT_TIMEZONE
    tz = ZoneInfo(tz_name)
    local = (now or datetime.now(tz)).astimezone(tz)
    today = local.date()
    out: list[str] = []
    for campaign in load_broadcasts()["campaigns"]:
        if not _campaign_matches_now(campaign, local):
            continue
        cid = str(campaign["id"])
        if was_fired(cid, today):
            continue
        out.append(cid)
    return out


def _telegram_token() -> str:
    try:
        from store.secrets import get_telegram_bot_token

        return get_telegram_bot_token()
    except Exception:
        from credentials import TELEGRAM_BOT_TOKEN as token

        return token


def _location_chats() -> dict[str, tuple[int, int] | None]:
    from store.locations import chats

    return chats()


def _broadcast_targets(campaign: dict[str, Any] | None = None) -> list[tuple[str, tuple[int, int]]]:
    mode = (campaign or {}).get("location_mode") or LOC_HOURLY
    keys_filter: set[str] | None = None
    if mode == LOC_KEYS:
        keys_filter = {str(k) for k in (campaign or {}).get("location_keys") or []}
    elif mode == LOC_HOURLY:
        try:
            from store.locations import hourly_png_location_keys

            keys_filter = set(hourly_png_location_keys())
        except Exception:
            keys_filter = None
    out: list[tuple[str, tuple[int, int]]] = []
    for key, chat in _location_chats().items():
        if chat is None:
            continue
        if keys_filter is not None and key not in keys_filter:
            continue
        out.append((key, chat))
    return out


def send_campaign(campaign_id: str, *, mark_state: bool = True) -> tuple[int, int]:
    """Send text to location chats. Returns (sent_count, skipped_count)."""
    campaign = campaign_by_id(campaign_id)
    if not campaign:
        raise KeyError(campaign_id)
    text = str(campaign.get("text") or "").strip()
    if not text:
        return 0, 0

    api = f"https://api.telegram.org/bot{_telegram_token()}/sendMessage"
    today = _local_today()
    targets = _broadcast_targets(campaign)
    sent = 0
    skipped = 0
    for location, (chat_id, thread_id) in targets:
        if mark_state and was_location_sent(campaign_id, location, today):
            sent += 1
            continue
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        try:
            r = requests.post(api, json=payload, timeout=30)
            if r.ok:
                sent += 1
                if mark_state:
                    mark_location_sent(campaign_id, location, today)
                print(f"✅ broadcast {campaign_id} → {location}")
            else:
                skipped += 1
                print(f"❌ broadcast {campaign_id} → {location}: HTTP {r.status_code}")
        except requests.exceptions.RequestException as e:
            skipped += 1
            print(f"❌ broadcast {campaign_id} → {location}: {safe_error(e)}")

    complete = bool(targets) and skipped == 0 and sent == len(targets)
    if complete and mark_state:
        mark_fired(campaign_id, today)

    repeat = campaign.get("repeat") or REPEAT_ONCE
    if complete and mark_state and repeat == REPEAT_ONCE and not campaign.get("builtin"):
        set_campaign_enabled(campaign_id, False)

    return sent, skipped


def _local_today() -> date:
    try:
        from store.schedules import load_schedules

        tz = ZoneInfo(load_schedules().get("timezone") or DEFAULT_TIMEZONE)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(tz).date()


def run_broadcast_tick() -> int:
    """Fire due campaigns; return count sent."""
    fired = 0
    for cid in due_campaign_ids():
        sent, _ = send_campaign(cid, mark_state=True)
        if sent:
            fired += 1
        else:
            print(f"⚠️ broadcast tick: {cid} — nothing sent")
    return fired
