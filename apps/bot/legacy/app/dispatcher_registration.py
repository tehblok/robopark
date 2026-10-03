"""Одобрение заявок и построение клавиатур админа."""

from __future__ import annotations

from html import escape

from config import DISPATCHER_DEFAULT_GREETING
from dispatcher_locations import (
    DISPATCHER_LOCATIONS,
    LOCATION_BY_KEY,
    profile_for_global,
    profile_for_location,
)
from dispatcher_pending import delete_pending, get_pending, update_pending
from dispatcher_roles import (
    ACCESS_GLOBAL,
    ROLE_MECHANIC,
    ROLE_OPERATOR,
)
from dispatcher_users_store import save_user


def format_pending_request(entry: dict, target_user_id: int) -> str:
    name = escape(entry.get("display_name") or "?")
    username = entry.get("username") or ""
    uname = f"@{escape(username)}" if username else "—"
    lines = [
        "🆕 <b>Заявка на доступ</b>",
        f"Обращение: {name}",
        f"TG: {uname} · id <code>{target_user_id}</code>",
    ]
    if entry.get("location_label"):
        lines.append(f"Локация: <b>{escape(str(entry['location_label']))}</b>")
    if entry.get("role") == ROLE_OPERATOR or entry.get("access") == ACCESS_GLOBAL:
        lines.append("Роль: <b>Оператор (global)</b>")
    elif entry.get("role") == ROLE_MECHANIC or entry.get("access") == "location":
        lines.append("Роль: <b>Механик (локация)</b>")
    return "\n".join(lines)


def location_keyboard(target_user_id: int) -> dict:
    rows: list[list[dict]] = []
    row: list[dict] = []
    for loc_name, info in DISPATCHER_LOCATIONS.items():
        row.append({
            "text": info["label"],
            "callback_data": f"loc:{target_user_id}:{info['key']}",
        })
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{
        "text": "🌐 Global",
        "callback_data": f"glob:{target_user_id}",
    }])
    rows.append([{
        "text": "❌ Отклонить",
        "callback_data": f"rej:{target_user_id}",
    }])
    return {"inline_keyboard": rows}


def greeting_keyboard(target_user_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📝 Приветствие по умолчанию",
                    "callback_data": f"grdef:{target_user_id}",
                },
                {
                    "text": "✏️ Своё приветствие",
                    "callback_data": f"grcust:{target_user_id}",
                },
            ],
            [{
                "text": "❌ Отклонить",
                "callback_data": f"rej:{target_user_id}",
            }],
        ]
    }


def greeting_text_for(entry: dict) -> str:
    if entry.get("greeting_mode") == "custom" and entry.get("custom_greeting"):
        text = entry["custom_greeting"]
        return text.replace("{name}", entry.get("display_name") or "")
    return DISPATCHER_DEFAULT_GREETING.format(name=entry.get("display_name") or "")


def build_user_profile(entry: dict) -> dict:
    profile: dict = {
        "name": entry.get("display_name") or "",
        "role": entry.get("role") or "mechanic",
        "access": entry.get("access") or "location",
        "allowed_tags": entry.get("allowed_tags"),
    }
    if entry.get("username"):
        profile["username"] = entry["username"]
    if entry.get("location"):
        profile["location"] = entry["location"]
    greeting = greeting_text_for(entry)
    profile["greeting"] = greeting
    return profile


def apply_location(target_user_id: int, location_key: str) -> dict | None:
    loc_name = LOCATION_BY_KEY.get(location_key)
    if not loc_name:
        return None
    role, access, location, tags = profile_for_location(loc_name)
    return update_pending(
        target_user_id,
        role=role,
        access=access,
        location=location,
        location_label=DISPATCHER_LOCATIONS[loc_name]["label"],
        allowed_tags=tags,
    )


def apply_global(target_user_id: int) -> dict | None:
    role, access, label, tags = profile_for_global()
    return update_pending(
        target_user_id,
        role=role,
        access=access,
        location=None,
        location_label=label,
        allowed_tags=tags,
    )


def pending_ready_to_approve(entry: dict | None) -> bool:
    if not entry:
        return False
    if not entry.get("allowed_tags"):
        return False
    if entry.get("greeting_mode") == "custom":
        return bool(entry.get("custom_greeting"))
    return entry.get("greeting_mode") == "default"


def approve_pending(target_user_id: int) -> dict | None:
    entry = get_pending(target_user_id)
    if not entry or not pending_ready_to_approve(entry):
        return None
    profile = build_user_profile(entry)
    save_user(target_user_id, profile)
    delete_pending(target_user_id)
    return profile
