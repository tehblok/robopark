"""Admin UI: Zoom reminder slots (parks/time/content/A-B) + hourly send window."""

from __future__ import annotations

import subprocess
import sys
import threading
from html import escape
from pathlib import Path
from typing import Any, Callable

from admin.nav import back_home_row, pair_row
from admin.sessions import clear_session, get_session, set_session
from paths import LOGS_DIR, ROOT
from store.broadcasts import LEGACY_TEXT_JOB_IDS
from store.locations import load_locations
from store.roles import is_full_admin
from store.schedules import (
    add_zoom_job,
    delete_job,
    job_by_id,
    load_schedules,
    send_window_label,
    set_job_fire_at,
    set_planner_anchor,
    set_send_window,
    toggle_job,
    update_job,
)
from store.zoom_jobs import (
    DAILY_TEXT,
    KIND_PLANNER_AB,
    KIND_SIMPLE,
    WEEKLY_DAYS,
    WEEKLY_TEXT,
    parks_summary,
)

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]

WEEKDAY_RU = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def apply_send_window_on_host() -> tuple[bool, str]:
    """Push schedules.json send_window into systemd timer (and cron if present).

    sudoers allows the script path itself, not `sudo bash script`.
    """
    script = ROOT / "deploy" / "apply_send_window.sh"
    last_err = ""
    for cmd in (["sudo", "-n", str(script)],):
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0:
                out = (proc.stdout or proc.stderr or "").strip() or "ok"
                return True, out
            last_err = (proc.stderr or proc.stdout or "").strip()
        except Exception as e:
            last_err = str(e)
    return False, last_err


def _apply_window_notice(ok: bool, detail: str) -> str:
    if ok:
        return f"systemd timer обновлён:\n<pre>{detail[:1500]}</pre>"
    return (
        "Окно записано в <code>data/schedules.json</code>, но таймер хоста не обновился.\n"
        "Нужен passwordless sudo для <code>deploy/apply_send_window.sh</code> "
        "(не <code>sudo bash …</code> — в sudoers путь к скрипту). "
        "Или SSH: <code>sudo " + str(ROOT) + "/deploy/apply_send_window.sh</code>\n"
        f"<pre>{detail[:1500]}</pre>"
    )


def _zoom_jobs() -> list[dict[str, Any]]:
    return [
        j
        for j in load_schedules()["jobs"]
        if j.get("id") not in LEGACY_TEXT_JOB_IDS
    ]


def _format_job_line(job: dict[str, Any]) -> str:
    flag = "✅" if job.get("enabled", True) else "⏸"
    wd = job.get("weekdays")
    if wd is None:
        days = "ежедн."
    else:
        days = ",".join(WEEKDAY_RU[d] for d in sorted(wd) if 0 <= int(d) <= 6)
    kind = "A/B" if (job.get("kind") or "") == KIND_PLANNER_AB else "фикс."
    parks = escape(parks_summary(job))
    label = escape(str(job.get("label") or job.get("id") or "?"))
    return (
        f"{flag} <code>{escape(str(job.get('fire_at') or '?'))}</code> · "
        f"{parks} · {label} ({days}, {kind})"
    )


def schedules_list_text() -> str:
    data = load_schedules()
    anchor = data.get("planner_anchor") or "2026-08-07"
    lines = [
        "<b>Расписание</b>",
        f"PNG-рассылка: {send_window_label()}",
        f"Zoom A/B якорь: <code>{escape(str(anchor))}</code> (чётные дни = A)",
        f"Тик: каждую минуту ({data.get('timezone', 'Europe/Moscow')})",
        "",
        "<b>Zoom-напоминания:</b>",
    ]
    for job in _zoom_jobs():
        lines.append(f"• {_format_job_line(job)}")
    lines.append("")
    lines.append("Один парк = один слот: время · текст · ссылка · чередование A/B.")
    return "\n".join(lines)


def schedules_list_keyboard() -> dict:
    rows: list[list[dict[str, str]]] = []
    for job in _zoom_jobs():
        jid = str(job.get("id") or "")
        if not jid:
            continue
        flag = "✅" if job.get("enabled", True) else "⏸"
        title = f"{flag} {job.get('fire_at')} · {parks_summary(job)}"
        if len(title) > 58:
            title = title[:57] + "…"
        rows.append([{"text": title, "callback_data": f"adm:sched:job:{jid}"}])
    rows.append([{"text": "➕ Новый Zoom", "callback_data": "adm:sched:new"}])
    rows.append([{"text": "🔀 Якорь A/B", "callback_data": "adm:sched:anchor"}])
    rows.append([{"text": "🕐 Окно PNG-рассылки", "callback_data": "adm:sched:sendwin"}])
    rows.append([{"text": "« Рассылки", "callback_data": "adm:menu:send"}])
    rows.append(back_home_row())
    return {"inline_keyboard": rows}


def _days_label(job: dict[str, Any]) -> str:
    wd = job.get("weekdays")
    if wd is None:
        return "каждый день"
    return ", ".join(WEEKDAY_RU[int(d)] for d in sorted(wd) if 0 <= int(d) <= 6)


def _job_detail_text(job_id: str) -> str:
    job = job_by_id(job_id)
    if not job:
        return "Не найдено"
    kind = job.get("kind") or KIND_SIMPLE
    alt = job.get("alternate")
    if alt in ("A", "B"):
        alt_label = f"только дни группы {alt} (якорь A/B)"
    else:
        alt_label = "каждый подходящий день (без A/B)"
    body = str(job.get("text") or "")
    preview = body if len(body) <= 900 else body[:899] + "…"
    locs = ", ".join(str(x) for x in (job.get("locations") or [])) or "—"
    link = str(job.get("link") or "")
    lines = [
        f"<b>{escape(str(job.get('label') or job_id))}</b>",
        f"id: <code>{escape(job_id)}</code>",
        f"парк: <b>{escape(locs)}</b>",
        f"время: <code>{escape(str(job.get('fire_at') or '?'))}</code>",
        f"дни: {_days_label(job)}",
        f"чередование: {alt_label}",
        f"статус: {'включено' if job.get('enabled', True) else 'выключено'}",
        f"ссылка: <code>{escape(link[:200] if link else '—')}</code>",
    ]
    if kind == KIND_PLANNER_AB:
        lines.append("⚠️ legacy A/B-слот (лучше разнести на отдельные парки)")
        groups = job.get("groups") or {}
        for g in ("A", "B"):
            entry = groups.get(g) or {}
            glocs = ", ".join(str(x) for x in (entry.get("locations") or [])) or "—"
            lines.append(
                f"<b>{g}</b>: {escape(glocs)} · "
                f"<code>{escape(str(entry.get('link') or '')[:80])}</code>"
            )
    lines.extend(["", "<b>Текст:</b>", f"<pre>{escape(preview)}</pre>"])
    return "\n".join(lines)


def _job_detail_keyboard(job_id: str) -> dict:
    job = job_by_id(job_id) or {}
    kind = job.get("kind") or KIND_SIMPLE
    alt = job.get("alternate")
    toggle_label = "⏸ Выключить" if job.get("enabled", True) else "▶️ Включить"
    rows: list[list[dict[str, str]]] = [
        [
            {"text": "✏️ Время", "callback_data": f"adm:sched:edit:{job_id}"},
            {"text": "🔗 Ссылка", "callback_data": f"adm:sched:link:{job_id}"},
        ],
        [{"text": "📝 Текст", "callback_data": f"adm:sched:text:{job_id}"}],
        [{"text": "🅿️ Парк", "callback_data": f"adm:sched:parks:{job_id}"}],
        [{"text": "📅 Дни недели", "callback_data": f"adm:sched:days:{job_id}"}],
    ]
    # Alternation: every day / A / B
    rows.append(
        [
            {
                "text": ("✅" if alt not in ("A", "B") else "⬜") + " каждый день",
                "callback_data": f"adm:sched:alt:{job_id}:none",
            },
        ]
    )
    rows.append(
        pair_row(
            {
                "text": ("✅" if alt == "A" else "⬜") + " только A",
                "callback_data": f"adm:sched:alt:{job_id}:A",
            },
            {
                "text": ("✅" if alt == "B" else "⬜") + " только B",
                "callback_data": f"adm:sched:alt:{job_id}:B",
            },
        )
    )
    if kind == KIND_PLANNER_AB:
        rows.append(
            pair_row(
                {"text": "🔗 Ссылка A", "callback_data": f"adm:sched:glink:{job_id}:A"},
                {"text": "🔗 Ссылка B", "callback_data": f"adm:sched:glink:{job_id}:B"},
            )
        )
        rows.append(
            [{"text": "➡️ В один парк (simple)", "callback_data": f"adm:sched:tokind:{job_id}:{KIND_SIMPLE}"}]
        )
    rows.append([{"text": toggle_label, "callback_data": f"adm:sched:toggle:{job_id}"}])
    rows.append([{"text": "📤 Отправить сейчас", "callback_data": f"adm:sched:fire:{job_id}"}])
    if str(job_id).startswith("zoom_"):
        rows.append([{"text": "🗑 Удалить", "callback_data": f"adm:sched:delask:{job_id}"}])
    rows.append([{"text": "« К списку", "callback_data": "adm:sched:list"}])
    return {"inline_keyboard": rows}


def _days_keyboard(job_id: str) -> dict:
    job = job_by_id(job_id) or {}
    wd = job.get("weekdays")
    selected = set(range(7)) if wd is None else {int(x) for x in wd}
    rows: list[list[dict[str, str]]] = []
    pair: list[dict[str, str]] = []
    for i, name in enumerate(WEEKDAY_RU):
        mark = "✅" if i in selected else "⬜"
        pair.append({"text": f"{mark} {name}", "callback_data": f"adm:sched:day:{job_id}:{i}"})
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        pair_row(
            {"text": "Каждый день", "callback_data": f"adm:sched:dayset:{job_id}:all"},
            {"text": "Ср–Пт", "callback_data": f"adm:sched:dayset:{job_id}:wfp"},
        )
    )
    rows.append([{"text": "« Назад", "callback_data": f"adm:sched:job:{job_id}"}])
    return {"inline_keyboard": rows}


def _parks_from_session(sess: dict[str, Any]) -> list[str]:
    return [str(x) for x in (sess.get("location_keys") or []) if x]


def _park_picker_text(sess: dict[str, Any]) -> str:
    job_id = sess.get("job_id")
    group = sess.get("group")
    extra = f", группа {escape(str(group))}" if group else ""
    n = len(_parks_from_session(sess))
    if job_id:
        head = f"Слот <code>{escape(str(job_id))}</code>{extra}"
    else:
        head = f"Новый слот{extra}"
    return f"{head}\nВыбрано парков: <b>{n}</b>\nОтметьте парки, затем «Сохранить»."


def _park_picker_keyboard(sess: dict[str, Any]) -> dict:
    selected = set(_parks_from_session(sess))
    rows: list[list[dict[str, str]]] = []
    pair: list[dict[str, str]] = []
    for loc in load_locations():
        key = loc["key"]
        slug = loc["slug"]
        mark = "✅" if key in selected else "⬜"
        pair.append(
            {
                "text": f"{mark} {loc['display_name']}",
                "callback_data": f"adm:sched:tg:{slug}",
            }
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append(
        pair_row(
            {"text": "Все", "callback_data": "adm:sched:pall"},
            {"text": "Сброс", "callback_data": "adm:sched:pnone"},
        )
    )
    rows.append([{"text": "💾 Сохранить", "callback_data": "adm:sched:parksave"}])
    cancel_cb = "adm:sched:list"
    job_id = sess.get("job_id")
    if job_id:
        cancel_cb = f"adm:sched:job:{job_id}"
    rows.append([{"text": "Отмена", "callback_data": cancel_cb}])
    return {"inline_keyboard": rows}


def _slug_to_key(slug: str) -> str | None:
    for loc in load_locations():
        if loc["slug"] == slug:
            return loc["key"]
    return None


def _is_park_session(sess: dict[str, Any] | None) -> bool:
    if not sess:
        return False
    return sess.get("kind") in ("sched_parks", "sched_new") and (
        sess.get("kind") == "sched_parks" or sess.get("step") == "parks"
    )


def _refresh_park_session(user_id: int, sess: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload = {k: v for k, v in sess.items() if k not in ("kind", "created_at")}
    payload.update(extra)
    set_session(user_id, str(sess.get("kind") or "sched_parks"), **payload)
    return get_session(user_id) or {}


def _start_park_pick(user_id: int, job_id: str, group: str | None = None) -> None:
    job = job_by_id(job_id) or {}
    if group:
        groups = job.get("groups") or {}
        entry = groups.get(group) or {}
        keys = list(entry.get("locations") or [])
    else:
        keys = list(job.get("locations") or [])
    set_session(
        user_id,
        "sched_parks",
        job_id=job_id,
        group=group,
        location_keys=keys,
    )


def _run_reminder(job_id: str, send: SendFn, user_id: int) -> None:
    py = ROOT / "venv" / "bin" / "python"
    if not py.is_file():
        py = Path(sys.executable)
    script = ROOT / "app" / "meeting_reminders.py"
    log_path = LOGS_DIR / "meeting_reminders_manual.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8") as logf:
            proc = subprocess.run(
                [str(py), str(script), job_id],
                cwd=str(ROOT),
                env={
                    **dict(**__import__("os").environ),
                    "PYTHONPATH": str(ROOT / "app"),
                    "TZ": "Europe/Moscow",
                },
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=False,
            )
        if proc.returncode == 0:
            send(user_id, f"✅ Напоминание <code>{job_id}</code> отправлено.", parse_mode="HTML")
        else:
            send(
                user_id,
                f"❌ Ошибка отправки <code>{job_id}</code> (код {proc.returncode}). "
                f"Лог: <code>logs/meeting_reminders_manual.log</code>",
                parse_mode="HTML",
            )
    except Exception as e:
        send(user_id, f"❌ Не удалось запустить: {e}")


def _convert_kind(job_id: str, new_kind: str) -> None:
    job = job_by_id(job_id)
    if not job:
        raise KeyError(job_id)
    if new_kind == KIND_PLANNER_AB:
        locs = list(job.get("locations") or [])
        link = str(job.get("link") or "")
        half = max(1, len(locs) // 2) if locs else 0
        a_locs = locs[:half] or locs
        b_locs = locs[half:] or list(locs)
        groups = {
            "A": {
                "label": ",".join(a_locs) if a_locs else "A",
                "locations": a_locs or [],
                "link": link,
            },
            "B": {
                "label": ",".join(b_locs) if b_locs else "B",
                "locations": b_locs or [],
                "link": link,
            },
        }
        update_job(job_id, kind=KIND_PLANNER_AB, groups=groups, locations=None, link=None)
        # Clear simple fields by rewriting job
        data = load_schedules()
        for row in data["jobs"]:
            if row.get("id") == job_id:
                row["kind"] = KIND_PLANNER_AB
                row["groups"] = groups
                row.pop("locations", None)
                row.pop("link", None)
                from store.schedules import save_schedules

                save_schedules(data)
                return
    else:
        groups = job.get("groups") or {}
        a = groups.get("A") or {}
        b = groups.get("B") or {}
        locs = list(dict.fromkeys(list(a.get("locations") or []) + list(b.get("locations") or [])))
        link = str(a.get("link") or b.get("link") or "")
        data = load_schedules()
        for row in data["jobs"]:
            if row.get("id") == job_id:
                row["kind"] = KIND_SIMPLE
                row["locations"] = locs
                row["link"] = link
                row.pop("groups", None)
                from store.schedules import save_schedules

                save_schedules(data)
                return
    raise KeyError(job_id)


def handle_schedules_callback(
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

    action = parts[2] if len(parts) > 2 else "list"

    if action == "list":
        answer(callback_id)
        clear_session(user_id)
        send(user_id, schedules_list_text(), parse_mode="HTML", reply_markup=schedules_list_keyboard())
        return True

    if action == "job" and len(parts) > 3:
        job_id = parts[3]
        answer(callback_id)
        clear_session(user_id)
        send(
            user_id,
            _job_detail_text(job_id),
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(job_id),
        )
        return True

    if action == "toggle" and len(parts) > 3:
        job_id = parts[3]
        state = toggle_job(job_id)
        answer(callback_id, "ok" if state is not None else "err")
        if state is None:
            send(user_id, "Не найдено")
            return True
        send(
            user_id,
            _job_detail_text(job_id),
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(job_id),
        )
        return True

    if action == "edit" and len(parts) > 3:
        job_id = parts[3]
        answer(callback_id)
        set_session(user_id, "sched_edit_time", job_id=job_id)
        send(
            user_id,
            f"Новое время для <code>{escape(job_id)}</code> в формате HH:MM (или /cancel).",
            parse_mode="HTML",
        )
        return True

    if action == "text" and len(parts) > 3:
        job_id = parts[3]
        answer(callback_id)
        set_session(user_id, "sched_edit_text", job_id=job_id)
        send(
            user_id,
            "Пришлите новый текст одним сообщением.\n"
            "Плейсхолдер ссылки: <code>{link}</code>\n/cancel — отмена.",
            parse_mode="HTML",
        )
        return True

    if action == "link" and len(parts) > 3:
        job_id = parts[3]
        answer(callback_id)
        job = job_by_id(job_id) or {}
        cur = str(job.get("link") or "")
        set_session(user_id, "sched_edit_link", job_id=job_id, group=None)
        send(
            user_id,
            "Пришлите новую ссылку Zoom одним сообщением (/cancel).\n"
            f"Сейчас: <code>{escape(cur[:300] if cur else '—')}</code>",
            parse_mode="HTML",
        )
        return True

    if action == "alt" and len(parts) > 4:
        job_id, mode = parts[3], parts[4]
        value = None if mode == "none" else mode
        if value not in (None, "A", "B"):
            answer(callback_id, "err")
            return True
        update_job(job_id, alternate=value)
        answer(callback_id, "ok")
        send(
            user_id,
            _job_detail_text(job_id),
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(job_id),
        )
        return True

    if action == "glink" and len(parts) > 4:
        job_id, group = parts[3], parts[4]
        answer(callback_id)
        set_session(user_id, "sched_edit_link", job_id=job_id, group=group)
        send(
            user_id,
            f"Ссылка Zoom для группы <b>{escape(group)}</b> (/cancel).",
            parse_mode="HTML",
        )
        return True

    if action == "parks" and len(parts) > 3:
        job_id = parts[3]
        answer(callback_id)
        _start_park_pick(user_id, job_id, None)
        sess = get_session(user_id) or {}
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if action == "gparks" and len(parts) > 4:
        job_id, group = parts[3], parts[4]
        answer(callback_id)
        _start_park_pick(user_id, job_id, group)
        sess = get_session(user_id) or {}
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if action == "tg" and len(parts) > 3:
        slug = parts[3]
        sess = get_session(user_id) or {}
        if not _is_park_session(sess):
            answer(callback_id, "нет сессии")
            return True
        key = _slug_to_key(slug)
        if not key:
            answer(callback_id, "нет")
            return True
        keys = _parks_from_session(sess)
        if key in keys:
            keys = [k for k in keys if k != key]
        else:
            keys.append(key)
        sess = _refresh_park_session(user_id, sess, location_keys=keys)
        answer(callback_id)
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if action == "pall":
        sess = get_session(user_id) or {}
        if not _is_park_session(sess):
            answer(callback_id, "нет сессии")
            return True
        keys = [loc["key"] for loc in load_locations()]
        sess = _refresh_park_session(user_id, sess, location_keys=keys)
        answer(callback_id, "все")
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if action == "pnone":
        sess = get_session(user_id) or {}
        if not _is_park_session(sess):
            answer(callback_id, "нет сессии")
            return True
        sess = _refresh_park_session(user_id, sess, location_keys=[])
        answer(callback_id, "сброс")
        send(
            user_id,
            _park_picker_text(sess),
            parse_mode="HTML",
            reply_markup=_park_picker_keyboard(sess),
        )
        return True

    if action == "parksave":
        sess = get_session(user_id) or {}
        keys = _parks_from_session(sess)

        # Creating a new Zoom job via wizard — one park per slot
        if sess.get("kind") == "sched_new" and sess.get("step") == "parks":
            if not keys:
                answer(callback_id, "нужен парк")
                send(user_id, "Выберите парк (достаточно одного).")
                return True
            link = str(sess.get("link") or "")
            label = str(sess.get("label") or "Zoom")
            fire_at = str(sess.get("fire_at") or "09:50")
            park = keys[0]
            try:
                row = add_zoom_job(
                    label=label,
                    fire_at=fire_at,
                    kind=KIND_SIMPLE,
                    text=DAILY_TEXT,
                    weekdays=None,
                    locations=[park],
                    link=link,
                    alternate=None,
                )
            except ValueError as e:
                answer(callback_id, "err")
                send(user_id, f"Ошибка: {e}")
                return True
            clear_session(user_id)
            answer(callback_id, "ok")
            jid = str(row["id"])
            note = ""
            if len(keys) > 1:
                note = (
                    "Взят первый парк; для остальных создайте отдельные слоты.\n\n"
                )
            send(
                user_id,
                note + _job_detail_text(jid),
                parse_mode="HTML",
                reply_markup=_job_detail_keyboard(jid),
            )
            return True

        job_id = str(sess.get("job_id") or "")
        group = sess.get("group")
        if not job_id:
            answer(callback_id, "err")
            return True
        if not keys:
            answer(callback_id, "нужен парк")
            send(user_id, "Выберите хотя бы один парк.")
            return True
        job = job_by_id(job_id)
        if not job:
            answer(callback_id, "нет")
            return True
        if group:
            groups = dict(job.get("groups") or {})
            entry = dict(groups.get(group) or {})
            entry["locations"] = keys[:1]
            if not entry.get("label"):
                entry["label"] = keys[0]
            groups[str(group)] = entry
            update_job(job_id, groups=groups)
        else:
            update_job(job_id, locations=[keys[0]])
            if len(keys) > 1:
                send(
                    user_id,
                    "Сохранён один парк на слот. Для второго — «➕ Новый Zoom».",
                )
        clear_session(user_id)
        answer(callback_id, "ok")
        send(
            user_id,
            _job_detail_text(job_id),
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(job_id),
        )
        return True

    if action == "days" and len(parts) > 3:
        job_id = parts[3]
        answer(callback_id)
        send(
            user_id,
            f"Дни для <code>{escape(job_id)}</code>:",
            parse_mode="HTML",
            reply_markup=_days_keyboard(job_id),
        )
        return True

    if action == "day" and len(parts) > 4:
        job_id, day_s = parts[3], parts[4]
        day = int(day_s)
        job = job_by_id(job_id) or {}
        wd = job.get("weekdays")
        selected = set(range(7)) if wd is None else {int(x) for x in wd}
        if day in selected:
            selected.discard(day)
        else:
            selected.add(day)
        if len(selected) == 7:
            update_job(job_id, weekdays=None)
        else:
            update_job(job_id, weekdays=sorted(selected))
        answer(callback_id)
        send(
            user_id,
            f"Дни для <code>{escape(job_id)}</code>:",
            parse_mode="HTML",
            reply_markup=_days_keyboard(job_id),
        )
        return True

    if action == "dayset" and len(parts) > 4:
        job_id, mode = parts[3], parts[4]
        if mode == "all":
            update_job(job_id, weekdays=None)
        elif mode == "wfp":
            update_job(job_id, weekdays=list(WEEKLY_DAYS))
        answer(callback_id, "ok")
        send(
            user_id,
            _job_detail_text(job_id),
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(job_id),
        )
        return True

    if action == "tokind" and len(parts) > 4:
        job_id, new_kind = parts[3], parts[4]
        try:
            _convert_kind(job_id, new_kind)
            answer(callback_id, "ok")
        except Exception as e:
            answer(callback_id, "err")
            send(user_id, f"Не удалось сменить режим: {e}")
            return True
        send(
            user_id,
            _job_detail_text(job_id),
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(job_id),
        )
        return True

    if action == "fire" and len(parts) > 3:
        job_id = parts[3]
        answer(callback_id, "отправка…")
        send(user_id, f"⏳ Отправляю <code>{escape(job_id)}</code>…", parse_mode="HTML")
        threading.Thread(
            target=_run_reminder,
            args=(job_id, send, user_id),
            daemon=True,
            name=f"reminder-{job_id}",
        ).start()
        return True

    if action == "delask" and len(parts) > 3:
        job_id = parts[3]
        answer(callback_id)
        send(
            user_id,
            f"Удалить слот <code>{escape(job_id)}</code>?",
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "🗑 Удалить", "callback_data": f"adm:sched:del:{job_id}"}],
                    [{"text": "Отмена", "callback_data": f"adm:sched:job:{job_id}"}],
                ]
            },
        )
        return True

    if action == "del" and len(parts) > 3:
        job_id = parts[3]
        ok = delete_job(job_id)
        answer(callback_id, "ok" if ok else "err")
        send(
            user_id,
            ("🗑 Удалено.\n\n" if ok else "Не удалось.\n\n") + schedules_list_text(),
            parse_mode="HTML",
            reply_markup=schedules_list_keyboard(),
        )
        return True

    if action == "anchor":
        answer(callback_id)
        data = load_schedules()
        set_session(user_id, "sched_anchor")
        send(
            user_id,
            "Якорь чередования A/B (дата ISO <code>YYYY-MM-DD</code>).\n"
            f"Сейчас: <code>{escape(str(data.get('planner_anchor') or ''))}</code>\n"
            "Чётные дни от якоря = группа A. /cancel — отмена.",
            parse_mode="HTML",
        )
        return True

    if action == "new":
        answer(callback_id)
        set_session(user_id, "sched_new", zoom_kind=KIND_SIMPLE, step="label")
        send(
            user_id,
            "Новый Zoom: <b>один парк</b> = свой слот (время, текст, ссылка).\n"
            "Название слота одним сообщением (/cancel).",
            parse_mode="HTML",
        )
        return True

    if action == "newkind" and len(parts) > 3:
        # Kept for old keyboards; always create simple one-park jobs.
        answer(callback_id)
        set_session(user_id, "sched_new", zoom_kind=KIND_SIMPLE, step="label")
        send(user_id, "Название слота одним сообщением (/cancel).", parse_mode="HTML")
        return True

    if action == "sendwin":
        answer(callback_id)
        sw = load_schedules()["send_window"]
        send(
            user_id,
            f"<b>Окно PNG-рассылки</b>\n"
            f"Сейчас: {send_window_label()}\n\n"
            f"start_hour=<code>{sw['start_hour']}</code>, end_hour=<code>{sw['end_hour']}</code>\n"
            f"Смена часа сразу обновляет systemd-таймер на хосте.",
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "Изменить start (ч)", "callback_data": "adm:sched:sw_start"}],
                    [{"text": "Изменить end (ч)", "callback_data": "adm:sched:sw_end"}],
                    [{"text": "Повторить apply на хост", "callback_data": "adm:sched:sw_apply"}],
                    [{"text": "« К списку", "callback_data": "adm:sched:list"}],
                ]
            },
        )
        return True

    if action == "sw_start":
        answer(callback_id)
        set_session(user_id, "sched_sw_start")
        send(user_id, "Час начала PNG-рассылки (0–23), например 9. /cancel — отмена.")
        return True

    if action == "sw_end":
        answer(callback_id)
        set_session(user_id, "sched_sw_end")
        send(user_id, "Час конца PNG-рассылки (0–23), например 21. /cancel — отмена.")
        return True

    if action == "sw_apply":
        answer(callback_id, "apply…")
        ok, detail = apply_send_window_on_host()
        if ok:
            send(user_id, f"<b>apply_send_window</b>\n<pre>{detail[:3500]}</pre>", parse_mode="HTML")
        else:
            send(
                user_id,
                "❌ Не удалось применить окно рассылки на хост.\n"
                "Нужен passwordless sudo для <code>deploy/apply_send_window.sh</code> "
                f"или SSH: <code>sudo {ROOT}/deploy/apply_send_window.sh</code>\n"
                f"<pre>{detail[:1500]}</pre>",
                parse_mode="HTML",
            )
        return True

    answer(callback_id)
    return True


def handle_schedules_text(*, user_id: int, text: str, send: SendFn) -> bool:
    sess = get_session(user_id)
    if not sess:
        return False
    kind = sess.get("kind")

    if kind == "sched_edit_time":
        job_id = sess.get("job_id")
        try:
            set_job_fire_at(str(job_id), text.strip())
        except (ValueError, KeyError) as e:
            send(user_id, f"Ошибка: {e}")
            return True
        clear_session(user_id)
        send(
            user_id,
            f"Сохранено: <code>{escape(str(job_id))}</code> → "
            f"<code>{escape(job_by_id(str(job_id))['fire_at'])}</code>",
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(str(job_id)),
        )
        return True

    if kind == "sched_edit_text":
        job_id = str(sess.get("job_id") or "")
        body = text.strip()
        if not body:
            send(user_id, "Пустой текст.")
            return True
        try:
            update_job(job_id, text=body)
        except KeyError as e:
            send(user_id, f"Ошибка: {e}")
            return True
        clear_session(user_id)
        send(
            user_id,
            _job_detail_text(job_id),
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(job_id),
        )
        return True

    if kind == "sched_edit_link":
        job_id = str(sess.get("job_id") or "")
        group = sess.get("group")
        link = text.strip()
        job = job_by_id(job_id)
        if not job:
            send(user_id, "Слот не найден.")
            clear_session(user_id)
            return True
        if group:
            groups = dict(job.get("groups") or {})
            entry = dict(groups.get(str(group)) or {})
            entry["link"] = link
            groups[str(group)] = entry
            update_job(job_id, groups=groups)
        else:
            update_job(job_id, link=link)
        clear_session(user_id)
        send(
            user_id,
            _job_detail_text(job_id),
            parse_mode="HTML",
            reply_markup=_job_detail_keyboard(job_id),
        )
        return True

    if kind == "sched_anchor":
        try:
            set_planner_anchor(text.strip())
        except ValueError as e:
            send(user_id, f"Ошибка: {e}")
            return True
        clear_session(user_id)
        send(
            user_id,
            schedules_list_text(),
            parse_mode="HTML",
            reply_markup=schedules_list_keyboard(),
        )
        return True

    if kind == "sched_new":
        step = sess.get("step")
        zoom_kind = sess.get("zoom_kind") or KIND_SIMPLE
        if step == "label":
            label = text.strip() or "Zoom"
            set_session(
                user_id,
                "sched_new",
                zoom_kind=zoom_kind,
                step="time",
                label=label,
            )
            send(user_id, "Время HH:MM (например 09:50).")
            return True
        if step == "time":
            try:
                from store.schedules import normalize_fire_at

                fire_at = normalize_fire_at(text.strip())
            except ValueError as e:
                send(user_id, f"Ошибка: {e}")
                return True
            set_session(
                user_id,
                "sched_new",
                zoom_kind=zoom_kind,
                step="link",
                label=sess.get("label"),
                fire_at=fire_at,
            )
            send(
                user_id,
                "Ссылка Zoom (одной строкой). Для A/B сначала одна ссылка — потом можно развести по группам.",
            )
            return True
        if step == "link":
            link = text.strip()
            set_session(
                user_id,
                "sched_new",
                zoom_kind=zoom_kind,
                step="parks",
                label=sess.get("label"),
                fire_at=sess.get("fire_at"),
                link=link,
                location_keys=[],
                group="A" if zoom_kind == KIND_PLANNER_AB else None,
            )
            sess = get_session(user_id) or {}
            hint = "парки группы <b>A</b>" if zoom_kind == KIND_PLANNER_AB else "парки"
            send(
                user_id,
                f"Выберите {hint}:\n" + _park_picker_text(sess),
                parse_mode="HTML",
                reply_markup=_park_picker_keyboard(sess),
            )
            return True
        return True

    if kind == "sched_sw_start":
        try:
            hour = int(text.strip())
            set_send_window(start_hour=hour)
        except ValueError as e:
            send(user_id, f"Ошибка: {e}")
            return True
        clear_session(user_id)
        ok, detail = apply_send_window_on_host()
        send(
            user_id,
            f"start_hour → {hour}.\n{_apply_window_notice(ok, detail)}",
            parse_mode="HTML",
        )
        return True

    if kind == "sched_sw_end":
        try:
            hour = int(text.strip())
            set_send_window(end_hour=hour)
        except ValueError as e:
            send(user_id, f"Ошибка: {e}")
            return True
        clear_session(user_id)
        ok, detail = apply_send_window_on_host()
        send(
            user_id,
            f"end_hour → {hour}.\n{_apply_window_notice(ok, detail)}",
            parse_mode="HTML",
        )
        return True

    return False
