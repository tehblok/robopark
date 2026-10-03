"""Сбор данных из Tracker.

Колонки «В очереди с» / «В очереди до» — период в Tracker-статусе queued («В очереди»).
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import DATA_DIR
from tracker_api import get_status_changelog, search_issues

PORT_KEY = "homePort"
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")
LATEST_FILE = os.path.join(DATA_DIR, "latest_report.csv")
QUERY_TEXT = (
    'Queue: SDCFLEETOPS '
    'AND Priority: blocker '
    'AND Type: repair, service, calibration '
    'AND ('
    'Resolution: empty() '
    'OR (Resolution: fixed AND Resolved: today())'
    ')'
)

CSV_HEADER = [
    "Ключ",
    "Задача",
    "Статус",
    "Резолюция",
    "Создана",
    "Теги",
    "Порт приписки",
    "В очереди с",
    "В очереди до",
]

# Tracker: «В очереди» — key queued.
QUEUED_STATUS_KEY = "queued"

CHANGELOG_PER_PAGE = 50
CHANGELOG_MAX_PAGES = 20
_PERIOD_CACHE_LIMIT = 5_000
_PERIOD_CACHE_BYTES = 2 * 1024 * 1024
_REPORT_TIMEZONE = ZoneInfo("Europe/Moscow")


def _status_key(ref: Any) -> str | None:
    if ref is None:
        return None
    if isinstance(ref, dict):
        return ref.get("key") or None
    return None


def _next_changelog_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' not in part:
            continue
        start = part.find("<")
        end = part.find(">", start + 1)
        if start >= 0 and end > start:
            return part[start + 1 : end]
    return None


def _fetch_status_changelog(issue_key: str, session: Any = None) -> list[dict]:
    del session
    return get_status_changelog(issue_key)


def _status_events(entries: list[dict]) -> list[tuple[str, str | None, str | None]]:
    events: list[tuple[str, str | None, str | None]] = []
    for entry in entries:
        updated_at = entry.get("updatedAt")
        if not updated_at:
            continue
        for field in entry.get("fields") or []:
            if (field.get("field") or {}).get("id") != "status":
                continue
            events.append((
                updated_at,
                _status_key(field.get("from")),
                _status_key(field.get("to")),
            ))
    events.sort(key=lambda item: item[0])
    return events


def _last_queue_period(events: list[tuple[str, str | None, str | None]]) -> tuple[str, str]:
    """Последний период queued: (start, end). end пустой — период ещё открыт."""
    periods: list[tuple[str, str]] = []
    start: str | None = None

    for updated_at, from_key, to_key in events:
        if to_key == QUEUED_STATUS_KEY:
            start = updated_at
        elif from_key == QUEUED_STATUS_KEY and start:
            periods.append((start, updated_at))
            start = None

    if periods:
        return periods[-1]

    if start:
        return start, ""
    return "", ""


def _resolve_queue_period(issue: dict, session: Any = None) -> tuple[str, str]:
    """Период в queued: start ISO, end ISO (пусто если сейчас в очереди)."""
    del session
    status_key = _status_key(issue.get("status"))

    if status_key == QUEUED_STATUS_KEY:
        started = issue.get("statusStartTime") or ""
        if not started:
            issue_key = issue.get("key")
            if issue_key:
                entries = get_status_changelog(issue_key)
                started, _ = _last_queue_period(_status_events(entries))
        return started, ""

    issue_key = issue.get("key")
    if not issue_key:
        return "", ""

    entries = get_status_changelog(issue_key)
    return _last_queue_period(_status_events(entries))


def _period_cache_path() -> str:
    return os.path.join(DATA_DIR, "queue-period-cache.json")


def _read_period_cache() -> dict[str, dict]:
    try:
        with open(_period_cache_path(), "rb") as stream:
            raw = stream.read(_PERIOD_CACHE_BYTES + 1)
        if len(raw) > _PERIOD_CACHE_BYTES:
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict) or len(payload) > _PERIOD_CACHE_LIMIT:
            return {}
        return {key: row for key, row in payload.items() if isinstance(key, str) and isinstance(row, dict)}
    except (OSError, ValueError, UnicodeError):
        return {}


def _write_period_cache(value: dict[str, dict]) -> None:
    raw = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if len(raw) > _PERIOD_CACHE_BYTES:
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=DATA_DIR, prefix=".queue-period-", delete=False) as stream:
            temporary = stream.name
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _period_cache_path())
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def _queue_period_with_cache(issue: dict, old: dict[str, dict], new: dict[str, dict]) -> tuple[str, str]:
    key = issue.get("key")
    status_key = _status_key(issue.get("status"))
    status_start = issue.get("statusStartTime")
    if isinstance(key, str) and isinstance(status_start, str) and status_start:
        cached = old.get(key)
        if (
            isinstance(cached, dict)
            and cached.get("status_key") == status_key
            and cached.get("status_start") == status_start
            and isinstance(cached.get("period"), list)
            and len(cached["period"]) == 2
            and all(isinstance(item, str) for item in cached["period"])
        ):
            new[key] = cached
            return cached["period"][0], cached["period"][1]
    period = _resolve_queue_period(issue)
    if isinstance(key, str) and isinstance(status_start, str) and status_start:
        new[key] = {"status_key": status_key, "status_start": status_start, "period": list(period)}
    return period


def collect() -> bool:
    """Скачать blocker-задачи через Robopark API. False = API недоступен."""

    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    if not os.path.exists(ARCHIVE_DIR):
        os.makedirs(ARCHIVE_DIR)

    print(f"🚀 [{datetime.now(_REPORT_TIMEZONE).strftime('%H:%M:%S')}] Начинаю сбор данных...")

    issues, err = search_issues(QUERY_TEXT, order="+created", max_pages=40)
    if err:
        print(f"❗ Ошибка API Tracker: {err} — PNG-рассылка остановлена")
        return False
    print(f"   📦 Найдено задач: {len(issues)}")

    rows: list[list] = []
    old_periods = _read_period_cache()
    new_periods: dict[str, dict] = {}
    with_period = 0
    in_queue_now = 0
    print(f"🔍 Разбираю период queued для {len(issues)} задач...")
    for idx, issue in enumerate(issues):
        queued_from, queued_to = _queue_period_with_cache(issue, old_periods, new_periods)
        if queued_from:
            with_period += 1
        if _status_key(issue.get("status")) == QUEUED_STATUS_KEY:
            in_queue_now += 1
        tags = issue.get("tags") or []
        if not isinstance(tags, list):
            tags = [tags]
        rows.append([
            issue.get("key"),
            issue.get("summary"),
            (issue.get("status") or {}).get("key", "") if isinstance(issue.get("status"), dict) else "",
            (issue.get("resolution") or {}).get("key", "") if isinstance(issue.get("resolution"), dict) else "",
            issue.get("createdAt", ""),
            ", ".join(str(t) for t in tags),
            issue.get(PORT_KEY, ""),
            queued_from,
            queued_to,
        ])
        if (idx + 1) % 10 == 0:
            print(f"   ⏳ Загружено: {idx + 1}/{len(issues)}")

    print(
        f"   📋 Период queued: {with_period}/{len(rows)} "
        f"(сейчас в queued: {in_queue_now})"
    )

    now = datetime.now(_REPORT_TIMEZONE)
    if now.hour == 21 and now.minute < 5 and os.path.exists(LATEST_FILE):
        snapshot_name = os.path.join(
            ARCHIVE_DIR, f"snapshot_{now.strftime('%Y%m%d_%H%M')}.csv"
        )
        shutil.copy(LATEST_FILE, snapshot_name)
        print(f"📸 Снимок для сравнения: {snapshot_name}")

    with open(LATEST_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)

    try:
        _write_period_cache(dict(list(new_periods.items())[:_PERIOD_CACHE_LIMIT]))
    except OSError:
        print("⚠️ Не удалось сохранить кэш истории очереди; следующий сбор прочитает её заново")

    print(f"✅ Файл {LATEST_FILE} обновлен.\n")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if collect() else 1)
