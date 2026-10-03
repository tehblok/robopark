"""Reminder and hourly-send schedule (data/schedules.json)."""

from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from store import DATA_DIR
from store.zoom_jobs import (
    KIND_PLANNER_AB,
    KIND_SIMPLE,
    LEGACY_COMBINED_IDS,
    WEEKLY_DAYS,
    WEEKLY_LEGACY_IDS,
    _EDITABLE_JOB_KEYS,
    default_zoom_jobs,
    parks_summary,
)

SCHEDULES_FILE = DATA_DIR / "schedules.json"
REMINDER_STATE_FILE = DATA_DIR / "reminder_state.json"

DEFAULT_TIMEZONE = "Europe/Moscow"

DEFAULT_SEND_WINDOW = {
    "start_hour": 9,
    "end_hour": 21,
    "enabled": True,
}

# Keep retrying an incomplete reminder this many minutes after fire_at.
JOB_RETRY_MINUTES = 15

DEFAULT_JOBS: list[dict[str, Any]] = default_zoom_jobs()

_lock = threading.RLock()


def _default_schedules() -> dict[str, Any]:
    return {
        "timezone": DEFAULT_TIMEZONE,
        "planner_anchor": "2026-08-07",
        "send_window": deepcopy(DEFAULT_SEND_WINDOW),
        "jobs": deepcopy(DEFAULT_JOBS),
    }


def _normalize_fire_at(value: str) -> str:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("ожидается HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("неверное время")
    return f"{hour:02d}:{minute:02d}"


def _enrich_job_from_default(row: dict[str, Any], default: dict[str, Any]) -> bool:
    """Fill missing Zoom fields from defaults; migrate Thu-only → Wed–Fri. Return changed."""
    changed = False
    for key in ("kind", "text", "link", "locations", "groups", "alternate"):
        if key not in row and key in default:
            row[key] = deepcopy(default[key])
            changed = True
    # Legacy Thursday-only weekly → Wed–Fri
    if row.get("id") in WEEKLY_LEGACY_IDS:
        wd = row.get("weekdays")
        if wd == [3] or wd == [3.0]:
            row["weekdays"] = list(WEEKLY_DAYS)
            if default.get("label"):
                row["label"] = default["label"]
            changed = True
        if not row.get("kind"):
            row["kind"] = KIND_SIMPLE
            changed = True
        for key in ("text", "link", "locations", "alternate"):
            if key not in row and key in default:
                row[key] = deepcopy(default[key])
                changed = True
    # One park per job: if somehow multiple locations stuck on a default morning id, keep first only
    locs = row.get("locations")
    if isinstance(locs, list) and len(locs) > 1 and str(row.get("id") or "").startswith("morning_"):
        row["locations"] = [locs[0]]
        changed = True
    return changed


def _extract_parks_from_legacy(stored: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map location key → {fire_at, link, text, alternate, enabled} from old A/B slots."""
    out: dict[str, dict[str, Any]] = {}
    for job in stored:
        jid = str(job.get("id") or "")
        if jid in ("daily_slot_1", "daily_slot_2", "daily_slot_3", "daily_slot_4"):
            groups = job.get("groups") or {}
            for side in ("A", "B"):
                entry = groups.get(side) or {}
                for loc in entry.get("locations") or []:
                    key = str(loc)
                    out[key] = {
                        "fire_at": job.get("fire_at"),
                        "link": entry.get("link") or "",
                        "text": job.get("text"),
                        "alternate": side,
                        "enabled": job.get("enabled", True),
                    }
        if jid == "kazan_nn_weekly":
            for loc in job.get("locations") or []:
                out[f"weekly:{loc}"] = {
                    "fire_at": job.get("fire_at"),
                    "link": job.get("link") or "",
                    "text": job.get("text"),
                    "alternate": None,
                    "enabled": job.get("enabled", True),
                    "weekdays": job.get("weekdays"),
                }
    return out


def _merge_jobs(stored: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], bool]:
    raw = [deepcopy(j) for j in (stored or []) if isinstance(j, dict) and j.get("id")]
    by_id = {j["id"]: j for j in raw}
    changed = False
    legacy_hit = any(jid in LEGACY_COMBINED_IDS for jid in by_id)
    default_ids = {d["id"] for d in DEFAULT_JOBS}
    missing_morning = any(
        str(d["id"]).startswith("morning_") and d["id"] not in by_id for d in DEFAULT_JOBS
    )
    park_overrides = _extract_parks_from_legacy(raw) if (legacy_hit or missing_morning) else {}

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for default in DEFAULT_JOBS:
        job_id = default["id"]
        seen.add(job_id)
        row = deepcopy(default)
        if job_id in by_id and job_id not in LEGACY_COMBINED_IDS:
            stored_row = by_id[job_id]
            row.update(
                {
                    k: v
                    for k, v in stored_row.items()
                    if k in row or k in _EDITABLE_JOB_KEYS
                }
            )
            if _enrich_job_from_default(row, default):
                changed = True
        else:
            # Apply park settings harvested from legacy combined slots
            locs = row.get("locations") or []
            loc = locs[0] if locs else None
            ov = None
            if loc and loc in park_overrides and str(job_id).startswith("morning_"):
                ov = park_overrides[loc]
            elif loc and f"weekly:{loc}" in park_overrides and "weekly" in job_id:
                ov = park_overrides[f"weekly:{loc}"]
            if ov:
                for key in ("fire_at", "link", "text", "alternate", "enabled", "weekdays"):
                    if key in ov and ov[key] is not None:
                        row[key] = deepcopy(ov[key])
                changed = True
            elif legacy_hit or missing_morning:
                changed = True
        merged.append(row)

    for job_id, row in by_id.items():
        if job_id in seen or job_id in LEGACY_COMBINED_IDS:
            if job_id in LEGACY_COMBINED_IDS:
                changed = True
            continue
        if not row.get("kind"):
            row["kind"] = KIND_PLANNER_AB if row.get("groups") else KIND_SIMPLE
            changed = True
        if "alternate" not in row:
            row["alternate"] = None
            changed = True
        merged.append(row)
        changed = True

    # Drop legacy combined if still somehow present
    before = len(merged)
    merged = [j for j in merged if j.get("id") not in LEGACY_COMBINED_IDS]
    if len(merged) != before:
        changed = True

    # Ensure new default ids exist when migrating
    have = {j["id"] for j in merged}
    for default in DEFAULT_JOBS:
        if default["id"] not in have:
            merged.append(deepcopy(default))
            changed = True

    # Stable-ish order: defaults first, then custom
    order = {d["id"]: i for i, d in enumerate(DEFAULT_JOBS)}
    merged.sort(key=lambda j: order.get(j.get("id"), 10_000))
    return merged, changed


def load_schedules(*, force_reload: bool = False) -> dict[str, Any]:
    del force_reload
    with _lock:
        if SCHEDULES_FILE.is_file():
            try:
                data = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        else:
            data = {}
        out = _default_schedules()
        persist = False
        if isinstance(data, dict):
            if data.get("timezone"):
                out["timezone"] = str(data["timezone"])
            if data.get("planner_anchor"):
                out["planner_anchor"] = str(data["planner_anchor"])
            if isinstance(data.get("send_window"), dict):
                sw = out["send_window"]
                src = data["send_window"]
                for key in ("start_hour", "end_hour", "enabled"):
                    if key in src:
                        sw[key] = src[key]
            jobs, jobs_changed = _merge_jobs(
                data.get("jobs") if isinstance(data.get("jobs"), list) else None
            )
            out["jobs"] = jobs
            persist = jobs_changed or not SCHEDULES_FILE.is_file()
        if persist or not SCHEDULES_FILE.is_file():
            _write_unlocked(out)
        return out


def _write_unlocked(data: dict[str, Any]) -> None:
    SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timezone": data.get("timezone") or DEFAULT_TIMEZONE,
        "planner_anchor": data.get("planner_anchor") or "2026-08-07",
        "send_window": data.get("send_window") or deepcopy(DEFAULT_SEND_WINDOW),
        "jobs": data.get("jobs") or deepcopy(DEFAULT_JOBS),
    }
    tmp = SCHEDULES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SCHEDULES_FILE)


def save_schedules(data: dict[str, Any]) -> None:
    with _lock:
        _write_unlocked(data)


def normalize_fire_at(value: str) -> str:
    return _normalize_fire_at(value)


def seed_schedules_file() -> Path:
    """Write default schedules.json if missing (install/heal/OTA)."""
    with _lock:
        if SCHEDULES_FILE.is_file():
            return SCHEDULES_FILE
        data = _default_schedules()
        _write_unlocked(data)
        return SCHEDULES_FILE


def job_by_id(job_id: str) -> dict[str, Any] | None:
    for job in load_schedules()["jobs"]:
        if job.get("id") == job_id:
            return deepcopy(job)
    return None


def toggle_job(job_id: str) -> bool | None:
    data = load_schedules()
    for job in data["jobs"]:
        if job.get("id") == job_id:
            job["enabled"] = not bool(job.get("enabled", True))
            save_schedules(data)
            return bool(job["enabled"])
    return None


def set_job_fire_at(job_id: str, fire_at: str) -> None:
    update_job(job_id, fire_at=_normalize_fire_at(fire_at))


def set_planner_anchor(value: str) -> str:
    """Set A/B alternation anchor date (ISO YYYY-MM-DD)."""
    raw = (value or "").strip()
    try:
        date.fromisoformat(raw)
    except ValueError as e:
        raise ValueError("ожидается YYYY-MM-DD") from e
    data = load_schedules()
    data["planner_anchor"] = raw
    save_schedules(data)
    return raw


def update_job(job_id: str, **fields: Any) -> dict[str, Any]:
    data = load_schedules()
    for job in data["jobs"]:
        if job.get("id") != job_id:
            continue
        for key, value in fields.items():
            if key == "fire_at" and value is not None:
                job["fire_at"] = _normalize_fire_at(str(value))
            elif key == "weekdays":
                job["weekdays"] = value
            elif key in _EDITABLE_JOB_KEYS:
                job[key] = value
        save_schedules(data)
        return deepcopy(job)
    raise KeyError(job_id)


def delete_job(job_id: str) -> bool:
    data = load_schedules()
    before = len(data["jobs"])
    data["jobs"] = [j for j in data["jobs"] if j.get("id") != job_id]
    if len(data["jobs"]) >= before:
        return False
    save_schedules(data)
    return True


def add_zoom_job(
    *,
    label: str,
    fire_at: str,
    kind: str,
    text: str,
    weekdays: list[int] | None = None,
    locations: list[str] | None = None,
    link: str = "",
    groups: dict[str, Any] | None = None,
    alternate: str | None = None,
) -> dict[str, Any]:
    kind_norm = kind if kind in (KIND_PLANNER_AB, KIND_SIMPLE) else KIND_SIMPLE
    body = (text or "").strip()
    if not body:
        raise ValueError("пустой текст")
    alt = alternate if alternate in ("A", "B") else None
    row: dict[str, Any] = {
        "id": f"zoom_{uuid.uuid4().hex[:10]}",
        "label": (label or "Zoom").strip() or "Zoom",
        "enabled": True,
        "fire_at": _normalize_fire_at(fire_at),
        "weekdays": weekdays,
        "kind": kind_norm,
        "alternate": alt,
        "text": body,
    }
    if kind_norm == KIND_PLANNER_AB:
        if not groups or "A" not in groups or "B" not in groups:
            raise ValueError("нужны группы A и B")
        row["groups"] = deepcopy(groups)
    else:
        keys = [str(k) for k in (locations or []) if str(k).strip()]
        if not keys:
            raise ValueError("нужен хотя бы один парк")
        row["locations"] = [keys[0]]
        row["link"] = (link or "").strip()
    data = load_schedules()
    data["jobs"].append(row)
    save_schedules(data)
    return deepcopy(row)


def set_send_window(*, start_hour: int | None = None, end_hour: int | None = None, enabled: bool | None = None) -> dict[str, Any]:
    data = load_schedules()
    sw = data.setdefault("send_window", deepcopy(DEFAULT_SEND_WINDOW))
    if start_hour is not None:
        if not (0 <= int(start_hour) <= 23):
            raise ValueError("start_hour 0–23")
        sw["start_hour"] = int(start_hour)
    if end_hour is not None:
        if not (0 <= int(end_hour) <= 23):
            raise ValueError("end_hour 0–23")
        sw["end_hour"] = int(end_hour)
    if enabled is not None:
        sw["enabled"] = bool(enabled)
    if sw["start_hour"] > sw["end_hour"]:
        raise ValueError("start_hour > end_hour")
    save_schedules(data)
    return sw


def send_window_label() -> str:
    sw = load_schedules()["send_window"]
    if not sw.get("enabled", True):
        return "рассылка отключена в расписании"
    tz = load_schedules()["timezone"]
    return f"каждый час {sw['start_hour']:02d}:00–{sw['end_hour']:02d}:00 {tz}"


def in_send_window(now: datetime | None = None) -> bool:
    sw = load_schedules()["send_window"]
    if not sw.get("enabled", True):
        return False
    tz = schedule_timezone(load_schedules().get("timezone"))
    local = (now or datetime.now(tz)).astimezone(tz)
    start = int(sw.get("start_hour", 9))
    end = int(sw.get("end_hour", 21))
    return start <= local.hour <= end


def _load_state() -> dict[str, str]:
    if not REMINDER_STATE_FILE.is_file():
        return {}
    try:
        raw = json.loads(REMINDER_STATE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    REMINDER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REMINDER_STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(REMINDER_STATE_FILE)


def was_fired(job_id: str, on_date: date) -> bool:
    key = f"{job_id}:{on_date.isoformat()}"
    return _load_state().get(key) == "1"


def was_location_sent(job_id: str, location: str, on_date: date) -> bool:
    key = f"{job_id}:{on_date.isoformat()}:loc:{location}"
    return _load_state().get(key) == "1"


def mark_location_sent(job_id: str, location: str, on_date: date) -> None:
    with _lock:
        state = _load_state()
        state[f"{job_id}:{on_date.isoformat()}:loc:{location}"] = "1"
        _save_state(state)


def mark_fired(job_id: str, on_date: date) -> None:
    with _lock:
        state = _load_state()
        state[f"{job_id}:{on_date.isoformat()}"] = "1"
        # prune old keys (>14 days)
        cutoff = on_date.toordinal() - 14
        for k in list(state):
            if ":" in k:
                try:
                    d = date.fromisoformat(k.split(":")[1])
                    if d.toordinal() < cutoff:
                        del state[k]
                except (ValueError, IndexError):
                    pass
        _save_state(state)


def minutes_since_fire(job: dict[str, Any], now: datetime) -> int | None:
    return _minutes_since_fire(job, now)


def schedule_timezone(name: str | None = None) -> ZoneInfo:
    """Always resolve a usable zone; fall back to Europe/Moscow."""
    raw = (name or "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(raw)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def moscow_now() -> datetime:
    return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))


def _minutes_since_fire(job: dict[str, Any], now: datetime) -> int | None:
    fire_at = job.get("fire_at")
    if not fire_at:
        return None
    try:
        hour, minute = map(int, str(fire_at).split(":", 1))
    except ValueError:
        return None
    # Compare as wall-clock minutes in the same tz as `now` (already localized).
    return (now.hour * 60 + now.minute) - (hour * 60 + minute)


def planner_group_for_date(on_date: date | None = None) -> str:
    """A/B day from planner_anchor (even offset → A)."""
    tz = schedule_timezone(load_schedules().get("timezone"))
    d = on_date or datetime.now(tz).date()
    raw = load_schedules().get("planner_anchor") or "2026-08-07"
    try:
        anchor = date.fromisoformat(str(raw))
    except ValueError:
        anchor = date(2026, 8, 7)
    return "A" if (d - anchor).days % 2 == 0 else "B"


def _job_matches_now(job: dict[str, Any], now: datetime) -> bool:
    if not job.get("enabled", True):
        return False
    weekdays = job.get("weekdays")
    if weekdays is not None and now.weekday() not in weekdays:
        return False
    alt = job.get("alternate")
    if alt in ("A", "B") and planner_group_for_date(now.date()) != alt:
        return False
    delta = _minutes_since_fire(job, now)
    if delta is None:
        return False
    return 0 <= delta <= JOB_RETRY_MINUTES


def due_jobs(now: datetime | None = None) -> list[str]:
    schedules = load_schedules()
    tz = schedule_timezone(schedules.get("timezone"))
    local = (now or datetime.now(tz)).astimezone(tz)
    today = local.date()
    out: list[str] = []
    for job in schedules["jobs"]:
        if not _job_matches_now(job, local):
            continue
        job_id = str(job["id"])
        if was_fired(job_id, today):
            continue
        from store.broadcasts import LEGACY_TEXT_JOB_IDS

        if job_id in LEGACY_TEXT_JOB_IDS:
            continue
        out.append(job_id)
    return out


def force_moscow_timezone() -> str:
    """Pin schedules.json timezone to Europe/Moscow (ops safety)."""
    data = load_schedules()
    if data.get("timezone") != DEFAULT_TIMEZONE:
        data["timezone"] = DEFAULT_TIMEZONE
        save_schedules(data)
    return DEFAULT_TIMEZONE
