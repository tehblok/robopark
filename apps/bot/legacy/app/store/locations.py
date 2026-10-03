"""Location settings atom: chats, tags, participation flags.

Three identity strings stay distinct:
  key           — chat map key, e.g. \"Север (Москва)\"
  display_name  — admin UI label, e.g. \"Север\"
  tracker_tag   — Tracker tag, e.g. \"МскСевер\"
"""

from __future__ import annotations

import json
import shutil
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from store import DATA_DIR, ROOT

LOCATIONS_FILE = DATA_DIR / "locations.json"
SIDECAR_FILE = DATA_DIR / "locations.sidecar.json"

PARTICIPATION_KEYS = (
    "hourly_png",
    "killswitch",
    "dispatcher_anchor",
    "sdcwh_zip",
    "robomaint_moves",
)

# Park text broadcasts are managed in /admin → Текст (data/broadcasts.json).

STATUS_TAGS = ("Донор",)

_lock = threading.RLock()
_cache: list[dict[str, Any]] | None = None


def _chat_pair(value: tuple[int, int] | None) -> dict[str, int | None] | None:
    if value is None:
        return None
    chat_id, thread_id = value
    return {"chat_id": int(chat_id), "thread_id": None if thread_id is None else int(thread_id)}


SEED_LOCATIONS: dict[str, dict[str, str]] = {
    "Next": {"label": "Next", "tag": "Next", "key": "next"},
    "РОКАЛАБ": {"label": "РОКАЛАБ", "tag": "РОКАЛАБ", "key": "rokalab"},
    "АРГАТЕХКАЗ": {"label": "АРГАТЕХКАЗ", "tag": "АРГАТЕХКАЗ", "key": "argatkaz"},
    "АРГАТЕХНН": {"label": "АРГАТЕХНН", "tag": "АРГАТЕХНН", "key": "argatnn"},
    "Юг": {"label": "Юг", "tag": "Юг", "key": "yug"},
    "Север (Москва)": {"label": "Север", "tag": "МскСевер", "key": "sever"},
    "КалиевАстана": {"label": "Астана", "tag": "КалиевАстана", "key": "astana"},
    "КалиевАлматы": {"label": "Алматы", "tag": "КалиевАлматы", "key": "almaty"},
    "Сигма": {"label": "Сигма", "tag": "Сигма", "key": "sigma"},
    "Континент": {"label": "Континент", "tag": "Континент", "key": "continent"},
    "АрмаМСК": {"label": "Арма", "tag": "АрмаМСК", "key": "arma"},
}


def default_locations() -> list[dict[str, Any]]:
    """Seed from current Python literals without merging identity strings."""
    from telegram_chats import (
        TELEGRAM_CHATS_PROD,
        TELEGRAM_CHATS_TEST,
        TELEGRAM_LOGISTICS_CHATS_PROD,
        TELEGRAM_LOGISTICS_CHATS_TEST,
    )

    killswitch_keys = {"Next", "Север (Москва)", "Сигма", "Континент"}
    locations: list[dict[str, Any]] = []
    for key, info in SEED_LOCATIONS.items():
        prod = TELEGRAM_CHATS_PROD.get(key)
        test = TELEGRAM_CHATS_TEST.get(key)
        logistics = None
        if key in TELEGRAM_LOGISTICS_CHATS_PROD or key in TELEGRAM_LOGISTICS_CHATS_TEST:
            logistics = {
                "prod": _chat_pair(TELEGRAM_LOGISTICS_CHATS_PROD.get(key)),
                "test": _chat_pair(TELEGRAM_LOGISTICS_CHATS_TEST.get(key)),
            }
        locations.append(
            normalize_location(
                {
                    "key": key,
                    "display_name": info["label"],
                    "tracker_tag": info["tag"],
                    "slug": info["key"],
                    "chats": {
                        "prod": _chat_pair(prod),
                        "test": _chat_pair(test),
                    },
                    "logistics": logistics,
                    "participation": {
                        "hourly_png": True,
                        "killswitch": key in killswitch_keys,
                        "dispatcher_anchor": True,
                        "sdcwh_zip": True,
                        "robomaint_moves": True,
                    },
                }
            )
        )
    return locations


def normalize_location(raw: dict[str, Any]) -> dict[str, Any]:
    key = str(raw.get("key") or "").strip()
    if not key:
        raise ValueError("location.key is required")
    display = str(raw.get("display_name") or raw.get("label") or key).strip()
    tag = str(raw.get("tracker_tag") or raw.get("tag") or key).strip()
    slug = str(raw.get("slug") or key).strip()
    chats_in = raw.get("chats") if isinstance(raw.get("chats"), dict) else {}
    part_in = raw.get("participation") if isinstance(raw.get("participation"), dict) else {}
    participation = {k: bool(part_in.get(k, True)) for k in PARTICIPATION_KEYS}
    if "killswitch" not in part_in:
        participation["killswitch"] = key in {
            "Next",
            "Север (Москва)",
            "Сигма",
            "Континент",
        }
    logistics = raw.get("logistics")
    if logistics is not None and not isinstance(logistics, dict):
        logistics = None
    return {
        "key": key,
        "display_name": display,
        "tracker_tag": tag,
        "slug": slug,
        "chats": {
            "prod": _normalize_chat(chats_in.get("prod")),
            "test": _normalize_chat(chats_in.get("test")),
        },
        "logistics": logistics,
        "participation": participation,
    }


def _as_chat_int(value: Any) -> int | None:
    """Parse chat_id / thread_id from int or string (incl. quoted Telegram JSON)."""
    if value is None or value is True or value is False:
        return None
    raw = str(value).strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _normalize_chat(value: Any) -> dict[str, int | None] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        cid = _as_chat_int(value[0])
        if cid is None:
            return None
        tid = _as_chat_int(value[1])
        return {"chat_id": cid, "thread_id": tid}
    if isinstance(value, dict):
        cid = _as_chat_int(value.get("chat_id", value.get("id")))
        if cid is None:
            return None
        # Admin / Telegram API paste may use message_thread_id instead of thread_id.
        tid_raw = value.get("thread_id")
        if tid_raw is None:
            tid_raw = value.get("message_thread_id")
        tid = _as_chat_int(tid_raw) if tid_raw is not None and tid_raw != "" else None
        return {"chat_id": cid, "thread_id": tid}
    return None


def _pair_from_chat(chat: dict[str, int | None] | None) -> tuple[int, int] | None:
    if not chat or chat.get("chat_id") is None:
        return None
    return (int(chat["chat_id"]), chat.get("thread_id"))


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _write_sidecar(locations: list[dict[str, Any]]) -> None:
    _write_atomic(
        SIDECAR_FILE,
        {
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "locations": locations,
        },
    )


def _load_sidecar() -> list[dict[str, Any]] | None:
    if not SIDECAR_FILE.is_file():
        return None
    try:
        payload = json.loads(SIDECAR_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = payload.get("locations") if isinstance(payload, dict) else payload
    if not isinstance(raw, list) or not raw:
        return None
    try:
        return [normalize_location(x) for x in raw if isinstance(x, dict)]
    except ValueError:
        return None


def _quarantine(path: Path) -> None:
    if not path.exists():
        return
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt.{ts}")
    try:
        path.replace(dest)
    except OSError:
        try:
            shutil.copy2(path, dest)
            path.unlink(missing_ok=True)
        except OSError:
            pass


def load_locations(*, force_reload: bool = False) -> list[dict[str, Any]]:
    global _cache
    with _lock:
        if _cache is not None and not force_reload:
            return deepcopy(_cache)
        if LOCATIONS_FILE.is_file():
            try:
                payload = json.loads(LOCATIONS_FILE.read_text(encoding="utf-8"))
                raw = payload.get("locations") if isinstance(payload, dict) else payload
                if isinstance(raw, list) and raw:
                    locations = [normalize_location(x) for x in raw if isinstance(x, dict)]
                    if locations:
                        locations, seeded = _merge_missing_seeds(locations)
                        if seeded:
                            # Persist new seed parks (e.g. КалиевАлматы) without wiping chats.
                            _write_atomic(
                                LOCATIONS_FILE,
                                {
                                    "version": 1,
                                    "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                                    "locations": locations,
                                    "status_tags": list(STATUS_TAGS),
                                },
                            )
                            _write_sidecar(locations)
                        _cache = locations
                        return deepcopy(_cache)
            except (OSError, json.JSONDecodeError, ValueError):
                _quarantine(LOCATIONS_FILE)
                recovered = _load_sidecar()
                if recovered:
                    recovered, _ = _merge_missing_seeds(recovered)
                    save_locations(recovered)
                    return deepcopy(recovered)
        locations = default_locations()
        save_locations(locations)
        return deepcopy(locations)


def _merge_missing_seeds(locations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Append SEED parks missing from stored locations.json (OTA-safe)."""
    by_key = {loc["key"]: loc for loc in locations}
    changed = False
    for seed in default_locations():
        if seed["key"] not in by_key:
            locations.append(seed)
            by_key[seed["key"]] = seed
            changed = True
    if _fill_incomplete_seed_chats(locations):
        changed = True
    return locations, changed


def _fill_incomplete_seed_chats(locations: list[dict[str, Any]]) -> bool:
    """Fill missing prod/test chat or forum thread from telegram_chats seeds.

    Does not overwrite a deliberately different chat_id. Fixes common case where
    chat_id is set but thread_id is missing (forum topics need message_thread_id).
    """
    from telegram_chats import TELEGRAM_CHATS_PROD, TELEGRAM_CHATS_TEST

    changed = False
    for loc in locations:
        key = loc["key"]
        for profile, seed_map in (("prod", TELEGRAM_CHATS_PROD), ("test", TELEGRAM_CHATS_TEST)):
            seed = seed_map.get(key)
            if not seed:
                continue
            seed_chat = _chat_pair(seed)
            if not seed_chat or seed_chat.get("chat_id") is None:
                continue
            chat = loc["chats"].get(profile)
            if chat is None or chat.get("chat_id") is None:
                loc["chats"][profile] = seed_chat
                changed = True
                continue
            if chat.get("thread_id") is None and seed_chat.get("thread_id") is not None:
                if int(chat["chat_id"]) == int(seed_chat["chat_id"]):
                    chat["thread_id"] = int(seed_chat["thread_id"])
                    changed = True
    return changed


def save_locations(locations: list[dict[str, Any]]) -> None:
    global _cache
    normalized = [normalize_location(x) for x in locations]
    with _lock:
        payload = {
            "version": 1,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "locations": normalized,
            "status_tags": list(STATUS_TAGS),
        }
        _write_atomic(LOCATIONS_FILE, payload)
        _write_sidecar(normalized)
        _cache = normalized


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def location_by_key(key: str) -> dict[str, Any] | None:
    for loc in load_locations():
        if loc["key"] == key:
            return loc
    return None


def location_by_tag(tag: str) -> dict[str, Any] | None:
    for loc in load_locations():
        if loc["tracker_tag"] == tag:
            return loc
    return None


def chats(profile: str | None = None) -> dict[str, tuple[int, int] | None]:
    from telegram_chats import resolve_profile

    p = profile or resolve_profile()
    if p not in ("prod", "test"):
        p = "prod"
    out: dict[str, tuple[int, int] | None] = {}
    for loc in load_locations():
        out[loc["key"]] = _pair_from_chat(loc["chats"].get(p))
    return out


def logistics_chats(profile: str | None = None) -> dict[str, tuple[int, int]]:
    from telegram_chats import resolve_profile

    p = profile or resolve_profile()
    if p not in ("prod", "test"):
        p = "prod"
    out: dict[str, tuple[int, int]] = {}
    for loc in load_locations():
        logistics = loc.get("logistics")
        if not isinstance(logistics, dict):
            continue
        pair = _pair_from_chat(_normalize_chat(logistics.get(p)))
        if pair and pair[1] is not None:
            out[loc["key"]] = (pair[0], int(pair[1]))
    return out


def tags() -> list[str]:
    """Tracker tags for filtering (includes status tags like Донор)."""
    seen: list[str] = []
    for loc in load_locations():
        tag = loc["tracker_tag"]
        if tag not in seen:
            seen.append(tag)
    for st in STATUS_TAGS:
        if st not in seen:
            seen.append(st)
    return seen


def service_tags() -> set[str]:
    """Park tags used for robot service classification (excludes Донор)."""
    return {loc["tracker_tag"] for loc in load_locations()}


def dispatcher_locations_map() -> dict[str, dict[str, str]]:
    return {
        loc["key"]: {
            "label": loc["display_name"],
            "tag": loc["tracker_tag"],
            "key": loc["slug"],
        }
        for loc in load_locations()
        if loc["participation"].get("dispatcher_anchor", True)
    }


def for_surface(name: str) -> list[dict[str, Any]]:
    return [loc for loc in load_locations() if loc["participation"].get(name, False)]


def any_participation(name: str) -> bool:
    return any(loc["participation"].get(name, False) for loc in load_locations())


def participation_enabled(location_key: str, name: str) -> bool:
    loc = location_by_key(location_key)
    if not loc:
        return False
    return bool(loc.get("participation", {}).get(name, False))


def hourly_png_location_keys() -> set[str]:
    return {loc["key"] for loc in for_surface("hourly_png")}


def killswitch_location_keys() -> set[str]:
    return {loc["key"] for loc in for_surface("killswitch")}


def update_location(key: str, **fields: Any) -> dict[str, Any]:
    locations = load_locations(force_reload=True)
    found = False
    for i, loc in enumerate(locations):
        if loc["key"] != key:
            continue
        merged = dict(loc)
        for field, value in fields.items():
            if field == "chats" and isinstance(value, dict):
                chats_merged = dict(merged.get("chats") or {})
                chats_merged.update(value)
                merged["chats"] = chats_merged
            elif field == "participation" and isinstance(value, dict):
                part = dict(merged.get("participation") or {})
                part.update(value)
                merged["participation"] = part
            else:
                merged[field] = value
        locations[i] = normalize_location(merged)
        found = True
        break
    if not found:
        raise KeyError(f"unknown location key: {key}")
    save_locations(locations)
    try:
        from dispatcher_locations import refresh_dispatcher_locations

        refresh_dispatcher_locations()
    except Exception:
        pass
    return location_by_key(key)  # type: ignore[return-value]
