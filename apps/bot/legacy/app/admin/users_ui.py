"""Admin users, full-admins, custom roles."""

from __future__ import annotations

from html import escape
from typing import Any, Callable

from admin.nav import back_home_row
from admin.sessions import clear_session, get_session, set_session
from dispatcher_roles import stored_role_label
from dispatcher_users_store import get_stored_user, load_all_users, save_user
from store.roles import (
    ALL_GRANTABLE,
    BUILTIN_ROLES,
    add_full_admin,
    admin_user_ids,
    is_full_admin,
    is_pinned_admin,
    remove_full_admin,
    role_definitions,
    upsert_role,
)

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]


def _users_home_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Роли", "callback_data": "adm:users:roles"}],
            [{"text": "+ full-admin", "callback_data": "adm:users:addadmin"}],
            [{"text": "− full-admin", "callback_data": "adm:users:rmadmin"}],
            [{"text": "Назначить роль user", "callback_data": "adm:users:setrole"}],
            back_home_row(),
        ]
    }


def handle_users_callback(
    *,
    user_id: int,
    parts: list[str],
    callback_id: str,
    send: SendFn,
    answer: AnswerFn,
) -> bool:
    if not is_full_admin(user_id):
        answer(callback_id, "Нет прав")
        return True

    action = parts[2] if len(parts) > 2 else ""

    if action == "roles":
        answer(callback_id)
        defs = role_definitions()
        lines = ["<b>Роли</b>", ""]
        for name, spec in sorted(defs.items()):
            perms = ", ".join(spec.get("permissions") or []) or "—"
            builtin = " (builtin)" if name in BUILTIN_ROLES else ""
            lines.append(f"• <code>{escape(name)}</code>{builtin} — {escape(spec.get('label') or name)}")
            lines.append(f"  {escape(perms)}")
        lines.append("\nСоздать/обновить: кнопка ниже → формат <code>name|label|perm1,perm2</code>")
        send(
            user_id,
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Upsert роль", "callback_data": "adm:users:newrole"}],
                    [{"text": "« К пользователям", "callback_data": "adm:users"}],
                ]
            },
        )
        return True

    if action == "newrole":
        answer(callback_id)
        set_session(user_id, "role_upsert")
        send(
            user_id,
            "Пришлите: <code>name|label|perm1,perm2,...</code>\n"
            f"Допустимые perm: <code>{', '.join(sorted(ALL_GRANTABLE))}</code>\n"
            "(host_ops нельзя — только full-admin id)\n/cancel",
            parse_mode="HTML",
        )
        return True

    if action == "addadmin":
        answer(callback_id)
        set_session(user_id, "admin_add_wait")
        send(user_id, "Пришлите USER_ID для full-admin, затем CONFIRM.\n/cancel")
        return True

    if action == "rmadmin":
        answer(callback_id)
        set_session(user_id, "admin_rm_wait")
        send(user_id, "Пришлите USER_ID для снятия full-admin, затем CONFIRM.\n/cancel")
        return True

    if action == "setrole":
        answer(callback_id)
        set_session(user_id, "user_set_role")
        names = ", ".join(sorted(role_definitions()))
        send(
            user_id,
            f"Пришлите: <code>USER_ID ROLE</code>\nРоли: <code>{escape(names)}</code>\n/cancel",
            parse_mode="HTML",
        )
        return True

    # default list
    answer(callback_id)
    users = load_all_users()
    admins = set(admin_user_ids())
    lines = [
        f"<b>Пользователи ({len(users)})</b>",
        f"Full admins: {', '.join(map(str, sorted(admins)))}",
        "",
    ]
    for uid in sorted(users):
        profile = users[uid]
        name = escape(profile.get("name") or "?")
        role = escape(stored_role_label(profile))
        suffix = ""
        if uid in admins:
            suffix = " · <b>full-admin</b>"
        if is_pinned_admin(uid):
            suffix += " · закреплён"
        lines.append(f"• {name} — {role}{suffix}\n  <code>{uid}</code>")
    send(user_id, "\n".join(lines), parse_mode="HTML", reply_markup=_users_home_keyboard())
    return True


def handle_users_text(*, user_id: int, text: str, send: SendFn) -> bool:
    sess = get_session(user_id)
    if not sess:
        return False
    kind = sess.get("kind")
    raw = text.strip()
    if raw == "/cancel":
        clear_session(user_id)
        send(user_id, "Отменено.")
        return True

    if kind == "role_upsert":
        parts = raw.split("|")
        if len(parts) != 3:
            send(user_id, "Формат: name|label|perm1,perm2")
            return True
        name, label, perms_raw = (p.strip() for p in parts)
        perms = [p.strip() for p in perms_raw.split(",") if p.strip()]
        try:
            upsert_role(name, label, perms)
        except ValueError as e:
            send(user_id, f"Ошибка: {e}")
            return True
        clear_session(user_id)
        send(user_id, f"Роль <code>{escape(name)}</code> сохранена.", parse_mode="HTML")
        return True

    if kind == "admin_add_wait":
        try:
            target = int(raw)
        except ValueError:
            send(user_id, "Нужен числовой USER_ID")
            return True
        set_session(user_id, "admin_add_confirm", target_id=target)
        send(user_id, f"Добавить full-admin <code>{target}</code>? Отправьте CONFIRM.", parse_mode="HTML")
        return True

    if kind == "admin_add_confirm":
        if raw != "CONFIRM":
            send(user_id, "Нужно CONFIRM или /cancel")
            return True
        target = int(sess.get("target_id"))
        add_full_admin(target)
        clear_session(user_id)
        send(user_id, f"Full-admin добавлен: <code>{target}</code>", parse_mode="HTML")
        return True

    if kind == "admin_rm_wait":
        try:
            target = int(raw)
        except ValueError:
            send(user_id, "Нужен числовой USER_ID")
            return True
        if is_pinned_admin(target):
            clear_session(user_id)
            send(
                user_id,
                f"Этот admin закреплён в коде и не снимается: <code>{target}</code>",
                parse_mode="HTML",
            )
            return True
        set_session(user_id, "admin_rm_confirm", target_id=target)
        send(user_id, f"Снять full-admin с <code>{target}</code>? CONFIRM.", parse_mode="HTML")
        return True

    if kind == "admin_rm_confirm":
        if raw != "CONFIRM":
            send(user_id, "Нужно CONFIRM или /cancel")
            return True
        target = int(sess.get("target_id"))
        try:
            remove_full_admin(target, actor_id=user_id)
        except ValueError as e:
            if "pinned" in str(e):
                send(
                    user_id,
                    f"Этот admin закреплён в коде и не снимается: <code>{target}</code>",
                    parse_mode="HTML",
                )
            else:
                send(user_id, f"Ошибка: {e}")
            clear_session(user_id)
            return True
        clear_session(user_id)
        send(user_id, f"Full-admin снят: <code>{target}</code>", parse_mode="HTML")
        return True

    if kind == "user_set_role":
        parts = raw.split()
        if len(parts) != 2:
            send(user_id, "Формат: USER_ID ROLE")
            return True
        try:
            target = int(parts[0])
        except ValueError:
            send(user_id, "USER_ID должен быть числом")
            return True
        role_name = parts[1].strip()
        if role_name not in role_definitions():
            send(user_id, f"Неизвестная роль: {role_name}")
            return True
        profile = get_stored_user(target)
        if not profile:
            send(user_id, "Пользователя нет в whitelist — сначала одобрите заявку.")
            return True
        profile = dict(profile)
        profile["role"] = role_name
        if role_name == "operator" or "global_search" in (
            role_definitions().get(role_name) or {}
        ).get("permissions", []):
            profile["access"] = "global"
            profile["allowed_tags"] = "*"
            profile.pop("location", None)
        else:
            profile["access"] = "location"
            if profile.get("allowed_tags") == "*":
                profile["allowed_tags"] = []
        save_user(target, profile)
        clear_session(user_id)
        send(user_id, f"User <code>{target}</code> → роль <code>{escape(role_name)}</code>", parse_mode="HTML")
        return True

    return False
