"""Счётчик запросов робота: день / месяц / всего."""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

from html import escape

from config import DATA_DIR
from dispatcher_auth import get_user_profile

USAGE_FILE = Path(DATA_DIR) / "dispatcher_usage.json"
_lock = threading.Lock()


def _load() -> dict[str, dict]:
    if not USAGE_FILE.exists():
        return {}
    try:
        with USAGE_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save(data: dict[str, dict]) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _month_key(day_key: str) -> str:
    return day_key[:7]


def _month_count(entry: dict, month: str) -> int:
    daily = entry.get("daily") or {}
    return sum(count for day, count in daily.items() if day.startswith(month))


def _today_count(entry: dict, today: str) -> int:
    daily = entry.get("daily") or {}
    return int(daily.get(today, 0))


DAILY_KEEP_DAYS = 90


def prune_old_daily(keep_days: int = DAILY_KEEP_DAYS) -> int:
    """Drop per-day counters older than keep_days. Keeps `total`."""
    cutoff = (date.today() - timedelta(days=int(keep_days))).isoformat()
    removed = 0
    with _lock:
        data = _load()
        changed = False
        for entry in data.values():
            daily = dict(entry.get("daily") or {})
            kept = {day: count for day, count in daily.items() if str(day) >= cutoff}
            drop = len(daily) - len(kept)
            if drop:
                entry["daily"] = kept
                removed += drop
                changed = True
        if changed:
            _save(data)
    return removed


def record_robot_query(
    user_id: int,
    name: str | None = None,
    username: str | None = None,
) -> None:
    today = date.today().isoformat()
    key = str(user_id)
    with _lock:
        data = _load()
        entry = data.get(key) or {"daily": {}, "total": 0}
        daily = dict(entry.get("daily") or {})
        daily[today] = int(daily.get(today, 0)) + 1
        entry["daily"] = daily
        entry["total"] = int(entry.get("total", 0)) + 1
        if name:
            entry["name"] = name
        if username:
            entry["username"] = username.lstrip("@")
        data[key] = entry
        _save(data)


def user_stats(user_id: int) -> dict[str, int]:
    today = date.today().isoformat()
    month = _month_key(today)
    with _lock:
        entry = _load().get(str(user_id)) or {}
    return {
        "today": _today_count(entry, today),
        "month": _month_count(entry, month),
        "total": int(entry.get("total", 0)),
    }


def _resolve_username(user_id: int, entry: dict) -> str | None:
    username = entry.get("username")
    if username:
        return str(username).lstrip("@")
    profile = get_user_profile(user_id)
    if profile and profile.get("username"):
        return str(profile["username"]).lstrip("@")
    return None


def _user_line(user_id: int, name: str, username: str | None) -> str:
    safe_name = escape(name)
    if username:
        uname = escape(username.lstrip("@"))
        nick = f'<a href="https://t.me/{uname}">@{uname}</a>'
        return f"{safe_name} · {nick} ({user_id})"
    return f'<a href="tg://user?id={user_id}">{safe_name}</a> ({user_id})'


def all_stats() -> list[tuple[int, str, str | None, dict[str, int]]]:
    today = date.today().isoformat()
    month = _month_key(today)
    with _lock:
        raw = _load()
    rows: list[tuple[int, str, str | None, dict[str, int]]] = []
    for uid_str, entry in raw.items():
        try:
            uid = int(uid_str)
        except ValueError:
            continue
        name = entry.get("name") or uid_str
        username = _resolve_username(uid, entry)
        rows.append((
            uid,
            name,
            username,
            {
                "today": _today_count(entry, today),
                "month": _month_count(entry, month),
                "total": int(entry.get("total", 0)),
            },
        ))
    rows.sort(key=lambda row: (-row[3]["total"], row[1].casefold()))
    return rows


def format_stats_report(*, target_user_id: int | None = None) -> str:
    today = date.today().isoformat()
    month = _month_key(today)
    rows = all_stats()
    if target_user_id is not None:
        rows = [row for row in rows if row[0] == target_user_id]
        if not rows:
            return f"Нет запросов от пользователя {target_user_id}."

    lines = [f"📊 Статистика запросов ({today}, месяц {month})"]
    sum_today = sum_m = sum_all = 0
    for uid, name, username, stats in rows:
        sum_today += stats["today"]
        sum_m += stats["month"]
        sum_all += stats["total"]
        lines.append(
            f"\n{_user_line(uid, name, username)}\n"
            f"  сегодня: {stats['today']} · месяц: {stats['month']} · всего: {stats['total']}"
        )
    if not rows:
        lines.append("\nПока нет запросов.")
    elif target_user_id is None:
        lines.append(
            f"\nИтого: сегодня {sum_today} · месяц {sum_m} · всего {sum_all}"
        )
    return "\n".join(lines)
