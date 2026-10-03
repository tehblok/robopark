"""Scheduled killswitch (СК) donut campaigns (data/sk_campaigns.json)."""

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

from killswitch_logic import aggregate_stats, campaign_query
from store import DATA_DIR
from store.broadcasts import REPEAT_LABELS, REPEAT_ONCE, REPEAT_WEEKDAYS
from store.schedules import DEFAULT_TIMEZONE, JOB_RETRY_MINUTES, _normalize_fire_at, minutes_since_fire

SK_FILE = DATA_DIR / "sk_campaigns.json"
SK_STATE_FILE = DATA_DIR / "sk_campaign_state.json"

_lock = threading.RLock()


def _default_payload() -> dict[str, Any]:
    return {"version": 1, "campaigns": []}


def _load_state() -> dict[str, str]:
    if not SK_STATE_FILE.is_file():
        return {}
    try:
        raw = json.loads(SK_STATE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    SK_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SK_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_sk(*, force_reload: bool = False) -> dict[str, Any]:
    del force_reload
    with _lock:
        data = _default_payload()
        if SK_FILE.is_file():
            try:
                raw = json.loads(SK_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("campaigns"), list):
                    data["campaigns"] = list(raw["campaigns"])
            except (OSError, json.JSONDecodeError):
                pass
        return deepcopy(data)


def _write_unlocked(data: dict[str, Any]) -> None:
    SK_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "campaigns": list(data.get("campaigns") or [])}
    tmp = SK_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SK_FILE)


def save_sk(data: dict[str, Any]) -> None:
    with _lock:
        _write_unlocked(data)


def campaign_by_id(campaign_id: str) -> dict[str, Any] | None:
    for row in load_sk()["campaigns"]:
        if row.get("id") == campaign_id:
            return row
    return None


def list_campaigns(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    rows = load_sk()["campaigns"]
    if enabled_only:
        return [r for r in rows if r.get("enabled")]
    return list(rows)


def add_campaign(
    *,
    tag: str,
    location_keys: list[str],
    fire_at: str,
    repeat: str,
    created_by: int | None = None,
) -> dict[str, Any]:
    query = campaign_query(tag)
    keys = [str(k).strip() for k in location_keys if str(k).strip()]
    if not keys:
        raise ValueError("нужен хотя бы один парк")
    repeat_norm = repeat if repeat in REPEAT_LABELS else REPEAT_ONCE
    fire_norm = _normalize_fire_at(fire_at)
    row = {
        "id": f"sk_{uuid.uuid4().hex[:10]}",
        "tag": tag.strip(),
        "query": query,
        "location_keys": keys,
        "enabled": True,
        "fire_at": fire_norm,
        "repeat": repeat_norm,
        "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": int(created_by) if created_by is not None else None,
    }
    data = load_sk()
    data["campaigns"].append(row)
    save_sk(data)
    return row


def set_campaign_enabled(campaign_id: str, enabled: bool) -> bool:
    data = load_sk()
    found = False
    for row in data["campaigns"]:
        if row.get("id") == campaign_id:
            row["enabled"] = bool(enabled)
            found = True
            break
    if found:
        save_sk(data)
    return found


def delete_campaign(campaign_id: str) -> bool:
    data = load_sk()
    before = len(data["campaigns"])
    data["campaigns"] = [r for r in data["campaigns"] if r.get("id") != campaign_id]
    if len(data["campaigns"]) >= before:
        return False
    save_sk(data)
    return True


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
    for campaign in load_sk()["campaigns"]:
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


def _locations() -> list[dict[str, Any]]:
    from store.locations import load_locations

    return load_locations()


def _chats() -> dict[str, tuple[int, int] | None]:
    from store.locations import chats

    return chats()


def _fetch_issues(tag: str) -> tuple[list[dict[str, Any]], str | None]:
    from tracker_api import search_issues

    return search_issues(campaign_query(tag), order="+created", max_pages=40)


def _render_campaign_pngs(
    campaign_id: str,
    tag: str,
    location_keys: list[str],
    issues: list[dict[str, Any]],
    locations: list[dict[str, Any]],
) -> dict[str, Path]:
    from generate_killswitch_progress import generate_all_progress

    out_dir = DATA_DIR / "sk_progress" / campaign_id
    return generate_all_progress(
        out_dir,
        issues=issues,
        locations=location_keys,
        title=tag,
        location_rows=locations,
    )


def _send_photo(chat_id: int, thread_id: int | None, png_path: Path, label: str) -> bool:
    token = (_telegram_token() or "").strip()
    if not token:
        print(f"❌ sk photo {label}: no telegram token")
        return False
    api = f"https://api.telegram.org/bot{token}/sendPhoto"
    data: dict[str, Any] = {"chat_id": chat_id}
    if thread_id is not None:
        data["message_thread_id"] = thread_id
    try:
        with png_path.open("rb") as f:
            r = requests.post(api, data=data, files={"photo": f}, timeout=60)
        if r.ok:
            print(f"✅ sk → {label}")
            return True
        print(f"❌ sk → {label}: HTTP {r.status_code}")
        return False
    except requests.exceptions.Timeout:
        print(f"⏳ sk → {label}: timeout (likely delivered)")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ sk → {label}: {safe_error(e)}")
        return False


def send_campaign(campaign_id: str, *, mark_state: bool = True) -> tuple[int, int]:
    """Build donuts and sendPhoto to selected parks. Returns (sent, skipped)."""
    campaign = campaign_by_id(campaign_id)
    if not campaign:
        raise KeyError(campaign_id)
    tag = str(campaign.get("tag") or "").strip()
    keys = [str(k) for k in (campaign.get("location_keys") or []) if k]
    if not tag or not keys:
        return 0, 0

    issues, err = _fetch_issues(tag)
    if err:
        print(f"❌ sk {campaign_id}: Tracker {err}")
        return 0, len(keys)

    locations = _locations()
    chats = _chats()
    stats = aggregate_stats(issues, keys, locations)
    paths = _render_campaign_pngs(campaign_id, tag, keys, issues, locations)

    today = _local_today()
    sent = 0
    skipped = 0
    for key in keys:
        if mark_state and was_location_sent(campaign_id, key, today):
            sent += 1
            continue
        chat = chats.get(key)
        png = paths.get(key)
        if chat is None or png is None or key not in stats:
            skipped += 1
            if png is None and key in stats:
                print(f"⏭️  sk {campaign_id} → {key}: нет PNG")
            continue
        chat_id, thread_id = chat
        if _send_photo(chat_id, thread_id, png, key):
            sent += 1
            if mark_state:
                mark_location_sent(campaign_id, key, today)
        else:
            skipped += 1

    complete = bool(keys) and skipped == 0 and sent == len(keys)
    if complete and mark_state:
        mark_fired(campaign_id, today)

    repeat = campaign.get("repeat") or REPEAT_ONCE
    if complete and mark_state and repeat == REPEAT_ONCE:
        set_campaign_enabled(campaign_id, False)

    return sent, skipped


def _local_today() -> date:
    try:
        from store.schedules import load_schedules

        tz = ZoneInfo(load_schedules().get("timezone") or DEFAULT_TIMEZONE)
    except Exception:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    return datetime.now(tz).date()


def run_sk_tick() -> int:
    fired = 0
    for cid in due_campaign_ids():
        sent, _ = send_campaign(cid, mark_state=True)
        if sent:
            fired += 1
        else:
            print(f"⚠️ sk tick: {cid} — nothing sent")
    return fired
