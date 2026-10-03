"""Заявки на доступ и состояние админа (ожидание кастомного приветствия).

JSON пишется атомарно (temp + replace), чтобы OTA/kill не оставляли битый файл.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from config import DATA_DIR

PENDING_FILE = Path(DATA_DIR) / "dispatcher_pending.json"
ADMIN_STATE_FILE = Path(DATA_DIR) / "dispatcher_admin_state.json"
USER_STATE_FILE = Path(DATA_DIR) / "dispatcher_registration_state.json"
_lock = threading.RLock()


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def get_pending(user_id: int) -> dict | None:
    with _lock:
        raw = _load_json(PENDING_FILE)
    entry = raw.get(str(user_id))
    return dict(entry) if entry else None


def save_pending(user_id: int, entry: dict) -> None:
    with _lock:
        raw = _load_json(PENDING_FILE)
        raw[str(user_id)] = entry
        _save_json(PENDING_FILE, raw)


def delete_pending(user_id: int) -> None:
    with _lock:
        raw = _load_json(PENDING_FILE)
        if str(user_id) in raw:
            del raw[str(user_id)]
            _save_json(PENDING_FILE, raw)


def create_pending(user_id: int, *, display_name: str, username: str | None, first_name: str | None) -> dict:
    entry = {
        "display_name": display_name,
        "username": username or "",
        "first_name": first_name or "",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "location": None,
        "location_label": None,
        "access": None,
        "role": None,
        "allowed_tags": None,
        "greeting_mode": None,
        "custom_greeting": None,
    }
    save_pending(user_id, entry)
    return entry


def update_pending(user_id: int, **fields: Any) -> dict | None:
    with _lock:
        raw = _load_json(PENDING_FILE)
        entry = raw.get(str(user_id))
        if not entry:
            return None
        entry = dict(entry)
        entry.update(fields)
        raw[str(user_id)] = entry
        _save_json(PENDING_FILE, raw)
        return dict(entry)


def user_registration_state(user_id: int) -> str | None:
    with _lock:
        raw = _load_json(USER_STATE_FILE)
    return raw.get(str(user_id))


def set_user_registration_state(user_id: int, state: str | None) -> None:
    with _lock:
        raw = _load_json(USER_STATE_FILE)
        key = str(user_id)
        if state is None:
            raw.pop(key, None)
        else:
            raw[key] = state
        _save_json(USER_STATE_FILE, raw)


def admin_awaiting_greeting(admin_id: int) -> int | None:
    with _lock:
        raw = _load_json(ADMIN_STATE_FILE)
    entry = raw.get(str(admin_id))
    if not entry:
        return None
    target = entry.get("awaiting_greeting_for")
    return int(target) if target is not None else None


def set_admin_awaiting_greeting(admin_id: int, target_user_id: int | None) -> None:
    with _lock:
        raw = _load_json(ADMIN_STATE_FILE)
        key = str(admin_id)
        if target_user_id is None:
            raw.pop(key, None)
        else:
            raw[key] = {"awaiting_greeting_for": target_user_id}
        _save_json(ADMIN_STATE_FILE, raw)
