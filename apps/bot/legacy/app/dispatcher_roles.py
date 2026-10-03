"""Роли пользователей диспетчер-бота.

Три уровня доступа (не путать друг с другом):

1. admin — только DISPATCHER_ADMIN_IDS в config.py.
   Админ-команды (/admin, /pause, /stats, одобрение заявок).
   Не задаётся полем role в JSON (нельзя «выдать админа» через профиль).

2. operator — глобальный поиск роботов (allowed_tags == "*", role == "operator").
   Операторы; без админ-команд.

3. mechanic — поиск только по своей локации (якорный тег, role == "mechanic").
   Механики; без админ-команд.

При новых фичах: проверять can_use_admin_commands / has_global_robot_search /
has_location_robot_search, а не allowed_tags напрямую.
"""

from __future__ import annotations

import re

ROLE_OPERATOR = "operator"
ROLE_MECHANIC = "mechanic"

ACCESS_GLOBAL = "global"
ACCESS_LOCATION = "location"

ROLE_LABELS = {
    ROLE_OPERATOR: "Оператор (global)",
    ROLE_MECHANIC: "Механик (локация)",
}


def _seed_admin_ids() -> list[int]:
    try:
        from config import DISPATCHER_ADMIN_IDS

        ids = [int(x) for x in DISPATCHER_ADMIN_IDS]
    except Exception:
        ids = []
    try:
        from config import PINNED_ADMIN_IDS

        for uid in PINNED_ADMIN_IDS:
            if int(uid) not in ids:
                ids.append(int(uid))
    except Exception:
        pass
    return ids


def is_admin_user(user_id: int) -> bool:
    """Pinned config IDs always win, even if roles.json is stale."""
    uid = int(user_id)
    if uid in _seed_admin_ids():
        return True
    try:
        from store.roles import is_full_admin

        return is_full_admin(uid)
    except Exception:
        return False


def is_admin(user_id: int, admin_ids: list[int] | None = None) -> bool:
    """Совместимость: is_admin(user_id, DISPATCHER_ADMIN_IDS)."""
    if admin_ids is None:
        return is_admin_user(user_id)
    return user_id in admin_ids


def infer_stored_role(profile: dict) -> str:
    """Вывести role operator/mechanic из профиля (legacy: access, allowed_tags)."""
    role = profile.get("role")
    if role in (ROLE_OPERATOR, ROLE_MECHANIC):
        return role
    if profile.get("access") == ACCESS_GLOBAL or profile.get("allowed_tags") == "*":
        return ROLE_OPERATOR
    return ROLE_MECHANIC


def normalize_user_profile(profile: dict) -> dict:
    """Единый вид профиля в dispatcher_users.json."""
    entry = dict(profile)
    tags = entry.get("allowed_tags")
    stored_role = entry.get("role")
    role = (
        stored_role
        if isinstance(stored_role, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", stored_role)
        else infer_stored_role(entry)
    )

    if role == ROLE_OPERATOR or (
        role != ROLE_MECHANIC and (entry.get("access") == ACCESS_GLOBAL or tags == "*")
    ):
        entry["role"] = role
        entry["access"] = ACCESS_GLOBAL
        entry["allowed_tags"] = "*"
        entry.pop("location", None)
    else:
        entry["role"] = role
        entry["access"] = ACCESS_LOCATION
        if tags == "*":
            entry["allowed_tags"] = []
        elif isinstance(tags, list):
            entry["allowed_tags"] = tags
        else:
            entry["allowed_tags"] = []

    return entry


def stored_role_label(profile: dict) -> str:
    role = infer_stored_role(profile)
    if role == ROLE_MECHANIC and profile.get("location"):
        return f"Механик · {profile['location']}"
    return ROLE_LABELS.get(role, role)


def can_use_admin_commands(user_id: int) -> bool:
    return is_admin_user(user_id)


def has_global_robot_search(user_id: int, profile: dict) -> bool:
    """Все задачи на роботе после якоря / без якоря для global."""
    if is_admin_user(user_id):
        return True
    try:
        from store.roles import PERM_GLOBAL_SEARCH, has_perm

        return has_perm(user_id, PERM_GLOBAL_SEARCH, profile)
    except Exception:
        return infer_stored_role(profile) == ROLE_OPERATOR


def has_location_robot_search(user_id: int, profile: dict) -> bool:
    if is_admin_user(user_id):
        return False
    try:
        from store.roles import PERM_GLOBAL_SEARCH, PERM_LOCATION_TAGS, has_perm

        if has_perm(user_id, PERM_GLOBAL_SEARCH, profile):
            return False
        return has_perm(user_id, PERM_LOCATION_TAGS, profile)
    except Exception:
        return infer_stored_role(profile) == ROLE_MECHANIC


def can_zip_wait(user_id: int, profile: dict) -> bool:
    try:
        from store.roles import PERM_ZIP_WAIT, has_perm

        return has_perm(user_id, PERM_ZIP_WAIT, profile)
    except Exception:
        return has_global_robot_search(user_id, profile)


def can_moves(user_id: int, profile: dict) -> bool:
    try:
        from store.roles import PERM_MOVES, has_perm

        return has_perm(user_id, PERM_MOVES, profile)
    except Exception:
        return True


def can_history(user_id: int, profile: dict) -> bool:
    try:
        from store.roles import PERM_HISTORY, has_perm

        return has_perm(user_id, PERM_HISTORY, profile)
    except Exception:
        return True


def can_qr(user_id: int, profile: dict) -> bool:
    try:
        from store.roles import PERM_QR, has_perm

        return has_perm(user_id, PERM_QR, profile)
    except Exception:
        return True


def allowed_location_tags(profile: dict, user_id: int | None = None) -> list[str]:
    uid = int(user_id) if user_id is not None else 0
    if has_location_robot_search(uid, profile):
        tags = profile.get("allowed_tags")
        if isinstance(tags, list):
            return tags
    return []
