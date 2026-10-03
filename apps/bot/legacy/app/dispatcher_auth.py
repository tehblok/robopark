"""Авторизация диспетчер-бота: whitelist и правило якорного тега."""

from __future__ import annotations

from config import DISPATCHER_DEFAULT_GREETING, DISPATCHER_USERS
from dispatcher_roles import (
    allowed_location_tags,
    has_global_robot_search,
)
from dispatcher_users_store import get_stored_user


def get_user_profile(user_id: int) -> dict | None:
    stored = get_stored_user(user_id)
    if stored is not None:
        return stored
    return DISPATCHER_USERS.get(user_id)


def greeting_for(profile: dict) -> str:
    return profile.get("greeting") or DISPATCHER_DEFAULT_GREETING.format(
        name=profile.get("name", "")
    )


def has_full_access(profile: dict) -> bool:
    """Оператор/global по профилю (без учёта admin ID). Устаревшее имя — prefer has_global_robot_search."""
    from dispatcher_roles import ROLE_OPERATOR, infer_stored_role

    return infer_stored_role(profile) == ROLE_OPERATOR


def has_robot_access(user_id: int, profile: dict, tasks: list[dict]) -> bool:
    """Есть ли доступ к роботу: global/админ или якорная задача механика."""
    if has_global_robot_search(user_id, profile):
        return True
    allowed_set = set(allowed_location_tags(profile, user_id))
    if not allowed_set:
        return False
    for task in tasks:
        task_tags = set(task.get("tags") or [])
        if task_tags & allowed_set:
            return True
    return False


def filter_tasks_for_user(user_id: int, profile: dict, tasks: list[dict]) -> list[dict]:
    """Вернуть задачи для показа или пустой список, если нет якоря."""
    if not has_robot_access(user_id, profile, tasks):
        return []
    return tasks
