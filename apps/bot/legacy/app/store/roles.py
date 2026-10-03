"""Unified roles: admin_user_ids + permission atoms + role definitions."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from store import DATA_DIR

ROLES_FILE = DATA_DIR / "roles.json"
ROLES_SIDECAR = DATA_DIR / "roles.sidecar.json"

# Permission atoms (KTD6). host_ops is NEVER on a custom role — only admin_user_ids.
PERM_GLOBAL_SEARCH = "global_search"
PERM_LOCATION_TAGS = "location_tags"
PERM_ZIP_WAIT = "zip_wait"
PERM_MOVES = "moves"
PERM_HISTORY = "history"
PERM_QR = "qr"
PERM_LOCATIONS_EDIT = "locations_edit"
PERM_USERS_ROLES = "users_roles"
PERM_PROFILE_SWITCH = "profile_switch"
PERM_PAUSE_MONITOR = "pause_monitor"
PERM_HOST_OPS = "host_ops"

DISPATCHER_PERMS = {
    PERM_GLOBAL_SEARCH,
    PERM_LOCATION_TAGS,
    PERM_ZIP_WAIT,
    PERM_MOVES,
    PERM_HISTORY,
    PERM_QR,
}

ADMIN_PERMS = {
    PERM_LOCATIONS_EDIT,
    PERM_USERS_ROLES,
    PERM_PROFILE_SWITCH,
    PERM_PAUSE_MONITOR,
}

ALL_GRANTABLE = DISPATCHER_PERMS | ADMIN_PERMS  # excludes host_ops

BUILTIN_ROLES: dict[str, dict[str, Any]] = {
    "operator": {
        "label": "Оператор (global)",
        "permissions": sorted(
            DISPATCHER_PERMS - {PERM_LOCATION_TAGS} | {PERM_GLOBAL_SEARCH}
        ),
    },
    "mechanic": {
        "label": "Механик (локация)",
        "permissions": sorted(
            {
                PERM_LOCATION_TAGS,
                PERM_ZIP_WAIT,
                PERM_MOVES,
                PERM_HISTORY,
                PERM_QR,
            }
        ),
    },
}

_lock = threading.RLock()
_cache: dict[str, Any] | None = None


def _write_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
        Path(tmp.name).replace(path)


PINNED_ADMIN_FALLBACK = frozenset()


def pinned_admin_ids() -> frozenset[int]:
    try:
        from config import PINNED_ADMIN_IDS

        return frozenset(int(x) for x in PINNED_ADMIN_IDS)
    except Exception:
        return PINNED_ADMIN_FALLBACK


def is_pinned_admin(user_id: int) -> bool:
    return int(user_id) in pinned_admin_ids()


def _bootstrap_admin_ids() -> list[int]:
    try:
        from config import DISPATCHER_ADMIN_IDS

        admin_ids = [int(x) for x in DISPATCHER_ADMIN_IDS]
    except Exception:
        admin_ids = []
    for uid in pinned_admin_ids():
        if uid not in admin_ids:
            admin_ids.append(uid)
    return admin_ids


def _default_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "admin_user_ids": _bootstrap_admin_ids(),
        "role_definitions": deepcopy(BUILTIN_ROLES),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def _ensure_bootstrap_admins(data: dict[str, Any]) -> bool:
    """Keep configured bootstrap admins without changing imported roles."""
    ids = [int(x) for x in data.get("admin_user_ids", [])]
    changed = False
    for uid in _bootstrap_admin_ids():
        if uid not in ids:
            ids.append(uid)
            changed = True
    if changed:
        data["admin_user_ids"] = ids
    return changed


def load_roles(*, force_reload: bool = False) -> dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None and not force_reload:
            return deepcopy(_cache)
        if ROLES_FILE.is_file():
            try:
                data = json.loads(ROLES_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "admin_user_ids" in data:
                    data.setdefault("role_definitions", deepcopy(BUILTIN_ROLES))
                    # Ensure builtins exist
                    for name, spec in BUILTIN_ROLES.items():
                        data["role_definitions"].setdefault(name, deepcopy(spec))
                    # Strip host_ops from any custom role
                    for name, spec in list(data["role_definitions"].items()):
                        perms = [
                            p
                            for p in spec.get("permissions", [])
                            if p in ALL_GRANTABLE
                        ]
                        spec["permissions"] = sorted(set(perms))
                    data["admin_user_ids"] = [int(x) for x in data["admin_user_ids"]]
                    if _ensure_bootstrap_admins(data):
                        try:
                            _write_atomic(ROLES_FILE, data)
                            _write_atomic(ROLES_SIDECAR, data)
                        except OSError:
                            pass
                    _cache = data
                    return deepcopy(_cache)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        data = _default_payload()
        _write_atomic(ROLES_FILE, data)
        _write_atomic(ROLES_SIDECAR, data)
        _cache = data
        return deepcopy(_cache)


def save_roles(payload: dict[str, Any]) -> None:
    global _cache
    data = deepcopy(payload)
    data["admin_user_ids"] = [int(x) for x in data.get("admin_user_ids", [])]
    data["updated_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    defs = data.setdefault("role_definitions", {})
    for name, spec in list(defs.items()):
        perms = [p for p in spec.get("permissions", []) if p in ALL_GRANTABLE]
        spec["permissions"] = sorted(set(perms))
    with _lock:
        _write_atomic(ROLES_FILE, data)
        _write_atomic(ROLES_SIDECAR, data)
        _cache = data


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def is_full_admin(user_id: int) -> bool:
    """Pinned seed IDs always win, even if roles.json is stale."""
    global _cache
    uid = int(user_id)
    if uid in _bootstrap_admin_ids():
        return True
    with _lock:
        if _cache is None:
            # load_roles acquires RLock again
            load_roles(force_reload=True)
        if _cache is None:
            return False
        return uid in _cache["admin_user_ids"]


def admin_user_ids() -> list[int]:
    with _lock:
        if _cache is None:
            load_roles(force_reload=True)
        if _cache is None:
            return list(pinned_admin_ids())
        ids = list(_cache["admin_user_ids"])
    for uid in pinned_admin_ids():
        if uid not in ids:
            ids.append(uid)
    return ids


def role_definitions() -> dict[str, dict[str, Any]]:
    return dict(load_roles()["role_definitions"])


def has_perm(user_id: int, atom: str, profile: dict | None = None) -> bool:
    if atom == PERM_HOST_OPS or atom in ADMIN_PERMS:
        return is_full_admin(user_id)
    if is_full_admin(user_id):
        return True
    if profile is None:
        return False
    role_name = profile.get("role") or "mechanic"
    defs = role_definitions()
    spec = defs.get(str(role_name))
    if not spec:
        # unknown role → mechanic-equivalent
        spec = defs.get("mechanic") or BUILTIN_ROLES["mechanic"]
    return atom in spec.get("permissions", [])


def add_full_admin(user_id: int) -> None:
    data = load_roles(force_reload=True)
    uid = int(user_id)
    if uid not in data["admin_user_ids"]:
        data["admin_user_ids"].append(uid)
        save_roles(data)


def remove_full_admin(user_id: int, *, actor_id: int | None = None) -> None:
    data = load_roles(force_reload=True)
    uid = int(user_id)
    if is_pinned_admin(uid):
        raise ValueError("pinned admin cannot be removed")
    ids = list(data["admin_user_ids"])
    if uid not in ids:
        return
    if len(ids) <= 1:
        raise ValueError("cannot remove the last full admin")
    if actor_id is not None and int(actor_id) == uid and len(ids) <= 1:
        raise ValueError("cannot demote self as last full admin")
    data["admin_user_ids"] = [x for x in ids if x != uid]
    save_roles(data)


def upsert_role(name: str, label: str, permissions: list[str]) -> None:
    name = name.strip()
    if not name or name in BUILTIN_ROLES:
        # Allow updating label/perms of custom only; builtins stay fixed atoms
        if name not in BUILTIN_ROLES:
            raise ValueError("empty role name")
    data = load_roles(force_reload=True)
    clean = sorted({p for p in permissions if p in ALL_GRANTABLE})
    data["role_definitions"][name] = {"label": label or name, "permissions": clean}
    save_roles(data)
