"""Whitelist диспетчер-бота: data/dispatcher_users.json + sidecar.

Пишем атомарно (temp + replace). Битый JSON не считаем «пустым списком» —
восстанавливаем из sidecar, иначе массовый wipe при следующем save_user.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from config import DATA_DIR, DISPATCHER_USERS
from dispatcher_roles import normalize_user_profile

USERS_FILE = Path(DATA_DIR) / "dispatcher_users.json"
SIDECAR_FILE = Path(DATA_DIR) / "dispatcher_users.sidecar.json"
_lock = threading.RLock()
log = logging.getLogger("dispatcher_users")


def _normalize_entry(raw: dict) -> dict:
    entry = dict(raw)
    if "allowed_tags" not in entry:
        entry["allowed_tags"] = []
    return normalize_user_profile(entry)


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _quarantine(path: Path) -> None:
    if not path.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt.{ts}")
    try:
        path.replace(dest)
        log.error("quarantined corrupt %s → %s", path.name, dest.name)
    except OSError:
        try:
            shutil.copy2(path, dest)
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_users_payload(payload: Any) -> dict[str, dict] | None:
    if not isinstance(payload, dict) or not payload:
        return None
    # Sidecar wraps users under "users"
    raw = payload.get("users") if "users" in payload and isinstance(payload.get("users"), dict) else payload
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, dict] = {}
    for key, value in raw.items():
        if key in ("version", "updated_at", "users"):
            continue
        if not isinstance(value, dict):
            continue
        try:
            out[str(key)] = _normalize_entry(value)
        except Exception:
            continue
    return out or None


def _load_sidecar() -> dict[str, dict] | None:
    if not SIDECAR_FILE.is_file():
        return None
    try:
        payload = json.loads(SIDECAR_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _parse_users_payload(payload)


def _write_sidecar(users: dict[str, dict]) -> None:
    _write_atomic(
        SIDECAR_FILE,
        {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "users": users,
        },
    )


def _recover_from_sidecar(*, reason: str) -> dict[str, dict]:
    side = _load_sidecar()
    if side:
        log.warning("%s — restored %d users from sidecar", reason, len(side))
        _persist(side)
        return side
    log.error("%s — sidecar empty, whitelist is empty", reason)
    return {}


def _load_raw() -> dict[str, dict]:
    """Load users. Corrupt main → sidecar. Never pretend corrupt == empty."""
    if USERS_FILE.is_file():
        try:
            payload = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.error("users.json unreadable (%s)", e)
            _quarantine(USERS_FILE)
            return _recover_from_sidecar(reason="users.json corrupt")
        parsed = _parse_users_payload(payload)
        if parsed is not None:
            return parsed
        # Empty {} is valid only when sidecar also empty
        if isinstance(payload, dict) and payload == {}:
            return _recover_from_sidecar(reason="users.json empty") if _load_sidecar() else {}
        log.error("users.json has no user entries")
        _quarantine(USERS_FILE)
        return _recover_from_sidecar(reason="users.json invalid shape")
    if SIDECAR_FILE.is_file():
        return _recover_from_sidecar(reason="users.json missing")
    return {}


def _persist(raw: dict[str, dict]) -> None:
    _write_atomic(USERS_FILE, raw)
    _write_sidecar(raw)


def _bootstrap_if_empty() -> None:
    """Seed pinned admins when store is missing or empty."""
    if not DISPATCHER_USERS:
        return
    if USERS_FILE.exists() or SIDECAR_FILE.exists():
        # Empty {} left from a bad heal/OTA must still get seed admins.
        raw = _load_raw()
        if raw:
            missing = {
                str(uid): _normalize_entry(profile)
                for uid, profile in DISPATCHER_USERS.items()
                if str(uid) not in raw
            }
            if missing:
                raw.update(missing)
                _persist(raw)
            return
    seeded = {str(uid): _normalize_entry(profile) for uid, profile in DISPATCHER_USERS.items()}
    _persist(seeded)


def load_all_users() -> dict[int, dict]:
    with _lock:
        _bootstrap_if_empty()
        raw = _load_raw()
    return {int(k): v for k, v in raw.items()}


def get_stored_user(user_id: int) -> dict | None:
    return load_all_users().get(int(user_id))


def save_user(user_id: int, profile: dict) -> None:
    with _lock:
        _bootstrap_if_empty()
        raw = _load_raw()
        raw[str(int(user_id))] = _normalize_entry(profile)
        _persist(raw)


def remove_user(user_id: int) -> None:
    with _lock:
        raw = _load_raw()
        key = str(int(user_id))
        if key not in raw:
            return
        del raw[key]
        _persist(raw)
