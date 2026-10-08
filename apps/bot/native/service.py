from __future__ import annotations

import re
import time
import uuid
from collections import OrderedDict
from datetime import date, datetime
from html import escape, unescape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from native.campaigns import render_campaign
from native.qr import render_robot_qr, yasadr_code
from native.reports import render_report, report_page_count, watchdog_parts
from native.task_cards import format_issues
from native.transport import ServiceError

ROBOT = re.compile(r"^(?:/robot\s+)?[aа]?(\d{1,6})$", re.IGNORECASE)
JOB_ID = re.compile(r"^[a-f0-9-]{36}$")
VIEWS = {
    "open": "Открытые задачи",
    "history": "История ремонтов",
    "moves": "Перемещения",
    "moves_history": "История перемещений",
    "parts": "Поставка запчастей",
}
WEEKDAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _aware_datetime(value):
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return value.strip()


def _apply_job_edit(job, field, raw_value):
    """Parse both /set and guided edits into one atomic job snapshot."""
    candidate = dict(job)
    value = raw_value.strip()
    if field in {"text", "url", "tracker_tag"}:
        candidate[field] = None if value.lower() == "none" else value
    elif field == "title":
        if not value or len(value) > 128:
            raise ValueError("invalid title")
        candidate[field] = value
    elif field == "weekdays":
        weekday_values = {
            name.lower(): index for index, name in enumerate(WEEKDAY_NAMES)
        }
        tokens = [item.strip().lower() for item in value.split(",") if item.strip()]
        try:
            days = [
                int(item) if item.isdigit() else weekday_values[item] for item in tokens
            ]
        except KeyError as error:
            raise ValueError("invalid weekdays") from error
        if (
            not days
            or len(days) != len(set(days))
            or any(day not in range(7) for day in days)
        ):
            raise ValueError("invalid weekdays")
        candidate[field] = sorted(days)
    elif field in {"start_hour", "end_hour"}:
        hour = int(value)
        if hour not in range(24):
            raise ValueError("invalid hour")
        candidate[field] = hour
    elif field == "time":
        if candidate["schedule"] == "hourly":
            match = re.fullmatch(r"(\d{1,2})\s*[-–]\s*(\d{1,2})", value)
            if match is None:
                raise ValueError("invalid hour range")
            start_hour, end_hour = map(int, match.groups())
            if not 0 <= start_hour <= end_hour <= 23:
                raise ValueError("invalid hour range")
            candidate.update(start_hour=start_hour, end_hour=end_hour, time=None)
        elif candidate["schedule"] == "once":
            candidate.update(run_at=_aware_datetime(value), time=None)
        else:
            if re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is None:
                raise ValueError("invalid time")
            candidate["time"] = value
    elif field == "timezone":
        if value.lower() in {"none", "inherit"}:
            candidate[field] = None
        else:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as error:
                raise ValueError("invalid timezone") from error
            candidate[field] = value
    elif field == "alternate":
        parts = value.split()
        alternate = parts[0].lower() if parts else ""
        alternate = {
            "a": "even",
            "а": "even",
            "b": "odd",
            "б": "odd",
        }.get(alternate, alternate)
        if alternate == "all" and len(parts) == 1:
            candidate.update(alternate="all", anchor_date=None)
        elif alternate in {"even", "odd"} and len(parts) in {1, 2}:
            anchor = parts[1] if len(parts) == 2 else candidate.get("anchor_date")
            if anchor is None:
                raise ValueError("anchor required")
            date.fromisoformat(str(anchor))
            candidate.update(alternate=alternate, anchor_date=str(anchor))
        else:
            raise ValueError("invalid alternation")
    elif field == "anchor_date":
        if value.lower() == "none":
            candidate[field] = None
        else:
            date.fromisoformat(value)
            candidate[field] = value
    elif field == "run_at":
        candidate.update(
            schedule="once",
            run_at=_aware_datetime(value),
            time=None,
            start_hour=None,
            end_hour=None,
            weekdays=[],
            alternate="all",
            anchor_date=None,
        )
    elif field == "schedule":
        parts = value.split(maxsplit=1)
        schedule = parts[0].lower()
        if schedule == "once":
            run_at = parts[1] if len(parts) == 2 else candidate.get("run_at")
            if not run_at:
                raise ValueError("run_at required")
            candidate = _apply_job_edit(candidate, "run_at", str(run_at))
        elif schedule == "daily":
            candidate.update(
                schedule="daily",
                time=candidate.get("time") or "09:00",
                run_at=None,
                start_hour=None,
                end_hour=None,
                weekdays=candidate.get("weekdays") or list(range(7)),
            )
        elif schedule == "hourly":
            candidate.update(
                schedule="hourly",
                time=None,
                run_at=None,
                start_hour=candidate.get("start_hour")
                if candidate.get("start_hour") is not None
                else 9,
                end_hour=candidate.get("end_hour")
                if candidate.get("end_hour") is not None
                else 21,
                weekdays=candidate.get("weekdays") or list(range(7)),
            )
        else:
            raise ValueError("invalid schedule")
    else:
        raise ValueError("invalid job field")
    return candidate


def parse_robot(value):
    hit = ROBOT.fullmatch(value.strip())
    return f"a{hit.group(1)}" if hit else None


def _keyboard(rows):
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in rows
        ]
    }


def _reply_keyboard(context):
    if context.get("role") == "mechanic":
        return {"remove_keyboard": True}
    rows = [["ℹ️ Помощь", "🔍 Робот"]]
    if context.get("can_manage"):
        rows += [["⚙️ Управление", "📊 Статус"], ["⏸ Пауза", "▶️ Старт"]]
    return {
        "keyboard": [[{"text": text} for text in row] for row in rows],
        "resize_keyboard": True,
    }


def _grid(buttons, width=3):
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


class BotService:
    def __init__(self, api, telegram):
        self.api, self.telegram = api, telegram
        self.pending = OrderedDict()
        self.sends = OrderedDict()
        self.approvals = OrderedDict()
        self.memberships = OrderedDict()

    def say(self, chat_id, text, *, rows=None, html=False, reply=None):
        payload = {
            "chat_id": chat_id,
            "text": text if html else text[:3900],
            "link_preview_options": {"is_disabled": True},
        }
        if rows:
            payload["reply_markup"] = _keyboard(rows)
        elif reply is not None:
            payload["reply_markup"] = reply
        if html:
            if len(text) > 3900:
                raise ValueError("HTML card is too long")
            payload["parse_mode"] = "HTML"
        return self.telegram.call("sendMessage", payload)

    def _help(self, chat_id, context):
        text = (
            "ℹ️ Помощь\n\nОтправьте номер робота, например 1460 или a1460.\n"
            "Под задачами — перемещения, история ремонтов, QR-код и ЗИП.\n\n"
            "/history НОМЕР — история ремонтов\n/moves НОМЕР — перемещения\n"
            "/parts НОМЕР — запчасти\n/qr НОМЕР — QR-код YASADR\n"
            "/stats — мои запросы\n/access — мой доступ и парки\n/cancel — отмена ввода"
        )
        if context.get("can_manage"):
            text += "\n\n⚙️ /admin — управление\n/requests — заявки на доступ\n/status — состояние\n/commands — команды настройки"
        return self.say(chat_id, text, reply=_reply_keyboard(context))

    def _manage(self, user_id):
        return self.api.call("GET", f"/manage?telegram_user_id={user_id}")

    def _join(self, chat_id):
        return self.say(
            chat_id,
            "Запросите доступ здесь, в Telegram. Выберите свою роль, затем парк — "
            "администратор рассмотрит заявку. Если доступ уже был, /access покажет его статус.",
            rows=[
                [("Механик", "n:join:mechanic:0"), ("Оператор", "n:join:operator:0")]
            ],
        )

    def _join_parks(self, chat_id, role, page):
        data = self.api.call("GET", "/onboarding/parks")
        parks = data["parks"]
        if not parks:
            return self.say(
                chat_id, "Парки ещё не настроены. Обратитесь к администратору."
            )
        page = min(page, (len(parks) - 1) // 12)
        rows = _grid(
            [
                (p["name"][:50], f"n:apply:{role}:{p['id']}")
                for p in parks[page * 12 : (page + 1) * 12]
            ]
        )
        navigation = []
        if page:
            navigation.append(("← Назад", f"n:join:{role}:{page - 1}"))
        if (page + 1) * 12 < len(parks):
            navigation.append(("Дальше →", f"n:join:{role}:{page + 1}"))
        if navigation:
            rows.append(navigation)
        return self.say(chat_id, "Выберите парк для заявки на доступ.", rows=rows)

    def _join_callback(self, user_id, chat_id, data, sender):
        match = re.fullmatch(r"n:(join|apply):(mechanic|operator):(\d{1,10})", data)
        if match is None:
            raise ValueError("invalid onboarding action")
        action, role, value = match.groups()
        number = int(value)
        if action == "join":
            return self._join_parks(chat_id, role, number)
        if number < 1:
            raise ValueError("invalid onboarding park")
        payload = {
            "telegram_user_id": user_id,
            "role": role,
            "park_id": number,
        }
        if isinstance(sender.get("username"), str):
            payload["telegram_username"] = sender["username"][:64]
        display_name = " ".join(
            sender[field]
            for field in ("first_name", "last_name")
            if isinstance(sender.get(field), str) and sender[field].strip()
        )
        if display_name:
            payload["display_name"] = display_name[:128]
        result = self.api.call("POST", "/onboarding/request", payload)
        text = {
            "pending": "Заявка отправлена администратору парка. /access — проверить статус.",
            "active": "Доступ к парку уже открыт. Отправьте номер робота для поиска задач.",
            "blocked": "Доступ отключён. Обратитесь к администратору; новая заявка не отменяет блокировку.",
            "rejected": "Заявка отклонена. Обратитесь к администратору парка.",
        }.get(result.get("state"), "Проверьте статус доступа: /access.")
        self.say(chat_id, text)
        for admin_id in result.get("notify_admin_ids", [])[:100]:
            try:
                self._requests(admin_id, admin_id, request_id=result.get("request_id"))
            except ServiceError:
                # A blocked Telegram DM must not roll back the employee's request.
                print("bot access notification unavailable", flush=True)

    def _access(self, user_id, chat_id):
        data = self.api.call("GET", f"/access?telegram_user_id={user_id}")
        labels = {
            "pending": "Ожидает одобрения",
            "approved": "Одобрен",
            "rejected": "Отклонён",
        }
        assigned = ", ".join(p["name"] for p in data["assigned_parks"]) or "пока нет"
        requests = data.get("requests", [])
        lines = [
            f"Доступ: {labels.get(data['access_status'], data['access_status'])}",
            f"Ваши парки: {assigned}",
        ]
        for row in requests[:15]:
            lines.append(
                f"{row['park']['name']}: {labels.get(row['status'], row['status'])}"
            )
        pending_parks = {
            row["park"]["id"] for row in requests if row["status"] == "pending"
        }
        rows = [
            [(p["name"][:50], f"n:request:{p['id']}")]
            for p in data["available_parks"]
            if p["id"] not in pending_parks
        ][:40]
        if rows:
            lines.append("Выберите парк, чтобы отправить заявку администратору.")
        if data["access_status"] == "approved":
            lines.append("Отправьте номер робота для поиска задач. /help — команды.")
        if data["access_status"] == "approved":
            self.say(
                chat_id,
                "✅ Доступ открыт. Отправьте номер робота.",
                reply=_reply_keyboard(data),
            )
        return self.say(chat_id, "\n".join(lines), rows=rows)

    def _request_heading(self, item):
        name = escape(str(item.get("display_name") or item["username"])[:128])
        username = item.get("telegram_username")
        identity = "@" + escape(str(username)) if username else "без username"
        telegram_id = item.get("telegram_user_id")
        return f"🆕 <b>Заявка на доступ</b>\nОбращение: {name}\nTG: {identity}" + (
            f" · id <code>{int(telegram_id)}</code>" if telegram_id else ""
        )

    def _requests(self, user_id, chat_id, page=0, request_id=None):
        data = self.api.call("GET", f"/access/requests?telegram_user_id={user_id}")
        if request_id is not None:
            data = [item for item in data if item["id"] == request_id]
            if not data:
                return
        if not data:
            return self.say(
                chat_id,
                "👥 Новых заявок нет.",
                rows=[[("← Управление", "n:menu:home")]],
            )
        state = self._manage(user_id)
        parks = state.get("parks", [])
        all_parks = self.api.call("GET", "/onboarding/parks").get("parks", [])
        can_global = bool(all_parks) and {p["id"] for p in all_parks} <= {
            p["id"] for p in parks
        }
        page = max(0, min(int(page), (len(data) - 1) // 5))
        for item in data[page * 5 : (page + 1) * 5]:
            role = {"mechanic": "Механик", "operator": "Оператор"}.get(
                item["role"], item["role"]
            )
            suffix = f"{item['id']}:{item['revision']}"
            choices = parks or [item["park"]]
            rows = _grid(
                [(p["name"][:45], f"n:select:{suffix}:{p['id']}") for p in choices[:48]]
            )
            if can_global and (
                item.get("user_access_status") == "pending"
                or item["role"] == "operator"
            ):
                rows.append([("🌐 Global", f"n:select:{suffix}:0")])
            rows.append([("❌ Отклонить", f"n:reject:{suffix}")])
            self.say(
                chat_id,
                self._request_heading(item)
                + f"\nЗапрошен парк: <b>{escape(item['park']['name'])}</b>"
                + f"\nРоль: <b>{escape(role)}</b>\n\nВыберите доступ:",
                rows=rows,
                html=True,
            )
        navigation = []
        if page:
            navigation.append(("← Назад", f"n:requests:{page - 1}"))
        if (page + 1) * 5 < len(data):
            navigation.append(("Далее →", f"n:requests:{page + 1}"))
        if navigation:
            self.say(chat_id, "Другие заявки", rows=[navigation])

    def _select_access(self, user_id, chat_id, request_id, revision, park_id):
        items = self.api.call("GET", f"/access/requests?telegram_user_id={user_id}")
        item = next(
            (r for r in items if r["id"] == request_id and r["revision"] == revision),
            None,
        )
        if item is None:
            raise ServiceError("request_already_resolved", status=409)
        state = self._manage(user_id)
        park = next((p for p in state.get("parks", []) if p["id"] == park_id), None)
        if park_id and park is None:
            raise ServiceError("park_out_of_scope", status=403)
        label = park["name"] if park else "🌐 Global"
        nonce = uuid.uuid4().hex[:16]
        self.approvals[user_id] = {
            "expires": time.monotonic() + 600,
            "nonce": nonce,
            "item": item,
            "park_id": park_id,
            "label": label,
            "custom": False,
        }
        self.approvals.move_to_end(user_id)
        while len(self.approvals) > 256:
            self.approvals.popitem(last=False)
        return self.say(
            chat_id,
            self._request_heading(item)
            + f"\nЛокация: <b>{escape(label)}</b>\nРоль: <b>"
            + (
                "Оператор (все парки)"
                if not park_id
                else "Механик (локация)"
                if item["role"] == "mechanic"
                else "Оператор"
            )
            + "</b>\n\nВыберите приветствие:",
            html=True,
            rows=[
                [
                    ("📝 Приветствие по умолчанию", f"n:greet:{nonce}"),
                    ("✏️ Своё приветствие", f"n:custom:{nonce}"),
                ],
                [("❌ Отклонить", f"n:reject:{request_id}:{revision}")],
            ],
        )

    def _approve_access(self, user_id, chat_id, flow, greeting=None):
        item = flow["item"]
        payload = {"approve": True, "revision": item["revision"]}
        if flow["park_id"]:
            payload["target_park_id"] = flow["park_id"]
        else:
            payload["global_access"] = True
        result = self.api.call(
            "POST",
            f"/access/requests/{item['id']}/decision?telegram_user_id={user_id}",
            payload,
        )
        self.approvals.pop(user_id, None)
        recipient = result["request"].get("telegram_user_id")
        name = result["request"].get("display_name") or result["request"]["username"]
        self.say(chat_id, f"✅ Одобрено: {name} · {flow['label']}")
        if recipient:
            try:
                self.say(
                    recipient,
                    greeting
                    or f"✅ Доступ открыт!\n\nЛокация: {flow['label']}\nОтправьте номер робота — покажу задачи и историю ремонта. /help — помощь.",
                    reply=_reply_keyboard({"role": result["request"]["role"]}),
                )
            except ServiceError:
                self.say(
                    chat_id,
                    "Доступ сохранён, но Telegram не доставил приветствие. Сотрудник может отправить /start.",
                )

    def _job(self, user_id, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ServiceError("job_not_found", status=404)
        state = self._manage(user_id)
        job = next((job for job in state["jobs"] if job["id"] == job_id), None)
        if not job:
            raise ServiceError("job_not_found", status=404)
        return job

    def _save_job(self, user_id, job):
        return self.api.call(
            "PUT", f"/manage/jobs/{job['id']}?telegram_user_id={user_id}", job
        )

    def _job_summary(self, job):
        schedule = job["schedule"]
        if schedule == "daily":
            timing = f"Ежедневно в {job.get('time') or '—'}"
        elif schedule == "hourly":
            timing = f"Каждый час с {job.get('start_hour')} до {job.get('end_hour')}"
        else:
            timing = f"Один раз: {job.get('run_at') or '—'}"
        weekdays = ", ".join(
            WEEKDAY_NAMES[day] for day in job.get("weekdays", []) if day in range(7)
        )
        timezone = job.get("timezone") or "как у парка"
        alternate = job.get("alternate", "all")
        if alternate == "all":
            alternation = "Без чередования"
        else:
            anchor = str(job.get("anchor_date") or "—")
            try:
                anchor = date.fromisoformat(anchor).strftime("%d.%m.%Y")
            except ValueError:
                pass
            alternation = (
                f"Группа {'A' if alternate == 'even' else 'B'} · опорная дата {anchor}"
            )
        return (
            f"{timing}\n"
            + (f"Дни: {weekdays or 'не используются'}\n" if schedule != "once" else "")
            + f"Часовой пояс: {timezone}\n{alternation}"
        )

    def _show_job(self, user_id, chat_id, job_id):
        job = self._job(user_id, job_id)
        prefix = f"{job['id']}:{job['revision']}"
        self.say(
            chat_id,
            f"{job['title']}\n{'Включено' if job['enabled'] else 'Выключено'}\n{self._job_summary(job)}\n"
            f"{job.get('text') or ''}\n{job.get('url') or ''}\nID: {job['id']}",
            rows=[
                [("Выключить" if job["enabled"] else "Включить", f"n:toggle:{prefix}")],
                [
                    ("Изменить время", f"n:time:{prefix}"),
                    ("Изменить текст", f"n:text:{prefix}"),
                ],
                [("Настройки", f"n:cfg:{prefix}")],
                [("Удалить", f"n:delete:{prefix}")],
                [("Отправить сейчас", f"n:send:{prefix}")],
            ],
        )

    def _show_job_settings(self, chat_id, job):
        prefix = f"{job['id']}:{job['revision']}"
        rows = [
            [("Название", f"n:title:{prefix}"), ("Ссылка", f"n:url:{prefix}")],
            [("Дни недели", f"n:days:{prefix}")],
            [("Часовой пояс", f"n:zone:{prefix}")],
            [("A/B и дата", f"n:alt:{prefix}")],
            [("Расписание", f"n:sched:{prefix}")],
        ]
        if job["kind"] in {"report", "campaign"}:
            rows.append([("Tracker-тег", f"n:tag:{prefix}")])
        rows.append([("← К заданию", f"n:job:{job['id']}")])
        return self.say(
            chat_id,
            f"Настройки «{job['title']}»\n{self._job_summary(job)}",
            rows=rows,
        )

    def _begin_job_edit(self, user_id, chat_id, job, field):
        self.pending[user_id] = {
            "expires": time.monotonic() + 300,
            "job_id": job["id"],
            "revision": job["revision"],
            "field": field,
        }
        self.pending.move_to_end(user_id)
        while len(self.pending) > 500:
            self.pending.popitem(last=False)
        prompts = {
            "title": "Отправьте новое название (1–128 символов).",
            "url": "Отправьте http(s)-ссылку или none, чтобы убрать её.",
            "weekdays": "Дни через запятую: Пн,Ср,Пт или 0,2,4 (0=Пн … 6=Вс).",
            "timezone": "Часовой пояс IANA, например Europe/Moscow; inherit — как у парка.",
            "alternate": "Формат: all, A 2026-10-05 (или even 2026-10-05), B 2026-10-05 (или odd 2026-10-05).",
            "schedule": "Формат: daily, hourly или once 2026-12-01T09:00:00+03:00.",
            "text": "Отправьте новый текст. Для отмены — /cancel.",
            "tracker_tag": "Отправьте Tracker-тег или none, чтобы убрать его.",
        }
        if field == "time":
            prompt = {
                "hourly": "Отправьте диапазон часов от 0 до 23, например 7-21.",
                "once": "Отправьте дату и время с поясом: 2026-12-01T09:00:00+03:00.",
            }.get(job["schedule"], "Отправьте новое время ЧЧ:ММ.")
        else:
            prompt = prompts[field]
        return self.say(chat_id, prompt + " /cancel — отмена.")

    def _search(self, user_id, chat_id, robot, view):
        if parse_robot(robot) is None or view not in VIEWS:
            return self.say(chat_id, "Отправьте точный номер робота, например 1460.")
        data = self.api.call(
            "GET", f"/robots/{robot}?telegram_user_id={user_id}&view={view}"
        )
        if data.get("greeting"):
            self.say(chat_id, unescape(data["greeting"]))
        allowed = data.get("allowed_views", list(VIEWS))
        rows = []
        if view != "open":
            rows.append([("← К задачам", f"n:open:{robot}")])
        for action, label in [
            ("moves", "🚚 Перемещение"),
            ("history", "📜 История ремонтов"),
            ("qr", "📱 QR-код"),
            ("parts", "📦 ЗИП"),
        ]:
            permitted = (
                data.get("can_qr", bool(data.get("issues")))
                if action == "qr"
                else action in allowed
            )
            if action != view and permitted:
                rows.append([(label, f"n:{action}:{robot}")])
        if view == "moves" and "moves_history" in allowed:
            rows.insert(1, [("📜 История перемещений", f"n:moves_history:{robot}")])
        return self.say(
            chat_id,
            format_issues(
                robot, data["issues"], view=view, truncated=data.get("truncated", False)
            ),
            rows=rows,
            html=True,
        )

    def _qr(self, user_id, chat_id, robot):
        code = yasadr_code(robot)
        # Same approval, park permissions and pause checks as other robot commands.
        data = self.api.call(
            "GET", f"/robots/{parse_robot(robot)}?telegram_user_id={user_id}&view=open"
        )
        if not data.get("can_qr", bool(data.get("issues"))):
            return self.say(
                chat_id, "Робот не найден в доступных вам задачах. QR-код недоступен."
            )
        return self.telegram.call(
            "sendPhoto",
            {"chat_id": chat_id, "caption": code},
            photo=render_robot_qr(robot),
        )

    def _stats(self, user_id, chat_id, *, all_users=False):
        data = self.api.call(
            "GET", f"/usage{'/all' if all_users else ''}?telegram_user_id={user_id}"
        )
        records = data if all_users else [data]
        lines = ["Запросы: сегодня / месяц / всего"]
        for row in records:
            stats = row["stats"]
            lines.append(
                f"{row['username'][:80]}: {stats['today']} / {stats['month']} / {stats['total']}"
            )
        # Bounded messages retain every visible user rather than truncating the list.
        block = []
        for line in lines:
            if sum(len(x) + 1 for x in block) + len(line) > 3700:
                self.say(chat_id, "\n".join(block))
                block = []
            block.append(line)
        return self.say(chat_id, "\n".join(block))

    def _users(self, user_id, chat_id, page=0, target_id=None):
        users = self.api.call("GET", f"/manage/users?telegram_user_id={user_id}")
        if target_id is not None:
            person = next((u for u in users if u["user_id"] == target_id), None)
            if person is None:
                raise ServiceError("user_not_found", status=404)
            rows = [
                [
                    (
                        "❌ Убрать доступ: " + p["name"][:35],
                        f"n:revoke:{target_id}:{p['id']}",
                    )
                ]
                for p in person["parks"][:48]
            ]
            rows.append([("← Люди", "n:users:0")])
            role = {
                "mechanic": "Механик",
                "operator": "Оператор",
                "admin": "Администратор",
                "royal": "Royal",
            }.get(person["role"], person["role"])
            return self.say(
                chat_id,
                f"👤 {person.get('display_name') or person['username']}\nРоль: {role}\nПарки: "
                + ", ".join(p["name"] for p in person["parks"]),
                rows=rows,
            )
        page = min(max(0, int(page)), max(0, (len(users) - 1) // 12))
        rows = [
            [((u.get("display_name") or u["username"])[:48], f"n:user:{u['user_id']}")]
            for u in users[page * 12 : (page + 1) * 12]
        ]
        navigation = []
        if page:
            navigation.append(("← Назад", f"n:users:{page - 1}"))
        if (page + 1) * 12 < len(users):
            navigation.append(("Далее →", f"n:users:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.append([("← Управление", "n:menu:people")])
        return self.say(
            chat_id,
            f"👥 Сотрудники ваших парков: {len(users)}\nВыберите сотрудника.",
            rows=rows,
        )

    def _control(self, user_id, chat_id, command, argument, context):
        if command != "/status" and context.get("role") != "royal":
            return self.say(chat_id, "Общую паузу сервиса меняет только royal.")
        if command != "/status" and argument not in {"queries", "deliveries", "all"}:
            return self.say(
                chat_id,
                "Формат: /pause или /resume, затем queries (запросы), deliveries (рассылки) или all.",
            )
        path = f"/control?telegram_user_id={user_id}"
        state = self.api.call("GET", path)
        if command != "/status":
            for scope in ("queries", "deliveries"):
                if argument in {scope, "all"}:
                    state[scope + "_paused"] = command == "/pause"
            state = self.api.call("PUT", path, state)
        return self.say(
            chat_id,
            "Запросы сотрудников: "
            + ("пауза" if state["queries_paused"] else "работают")
            + "\nРассылки: "
            + ("пауза" if state["deliveries_paused"] else "работают")
            + "\nАдминистраторы могут проверять запросы и управлять ботом во время паузы.",
        )

    def _admin_menu(self, user_id, chat_id, section="home"):
        state = self._manage(user_id)
        if section == "home":
            context = self.api.call("GET", f"/context?telegram_user_id={user_id}")
            self.say(chat_id, "⚙️ Управление", reply=_reply_keyboard(context))
            return self.say(
                chat_id,
                "Выберите раздел:",
                rows=[
                    [("📍 Локации", "n:menu:parks"), ("👥 Люди", "n:menu:people")],
                    [("📢 Рассылки и отчёты", "n:menu:sends")],
                    [("⚙️ Система", "n:menu:system")],
                    [("🛠 Хост и обновления", "n:menu:host")],
                ],
            )
        back = [("← Управление", "n:menu:home")]
        if section in {"parks", "sends"}:
            rows = _grid(
                [(p["name"][:45], f"n:park:{p['id']}") for p in state["parks"][:48]]
            )
            rows.append(back)
            return self.say(
                chat_id,
                "📍 Выберите локацию"
                if section == "parks"
                else "📢 Рассылки и отчёты\nВыберите парк: PNG, Zoom, СК, тексты и расписания.",
                rows=rows,
            )
        if section == "people":
            return self.say(
                chat_id,
                "👥 Люди",
                rows=[
                    [("🆕 Заявки на доступ", "n:requests:0")],
                    [("👥 Сотрудники", "n:users:0")],
                    [("📊 Статистика сотрудников", "n:menu:usage")],
                    back,
                ],
            )
        if section == "usage":
            return self._stats(user_id, chat_id, all_users=True)
        if section == "system":
            return self.say(
                chat_id,
                "⚙️ Система",
                rows=[
                    [("📊 Статус", "n:control:status")],
                    [("⏸ Пауза", "n:control:pause"), ("▶️ Старт", "n:control:resume")],
                    back,
                ],
            )
        if section == "host":
            return self.say(
                chat_id,
                "🛠 Хост и обновления\nОбновления, диагностика и токены доступны в Robopark → Система. Секреты вводятся в защищённой форме сайта.",
                rows=[back],
            )

    def _commands(self, chat_id):
        return self.say(
            chat_id,
            "Команды настройки\n"
            "/chat ПАРК CHAT_ID [THREAD_ID] — чат и тема\n"
            "/new ПАРК text|zoom|report|campaign НАЗВАНИЕ — создать выключенное задание\n"
            "/set ID поле значение — изменить задание\n"
            "/requests — одобрение доступа сотрудников\n"
            "/stats all — статистика сотрудников; /status — состояние паузы\n"
            "/pause или /resume queries|deliveries|all — пауза (royal)\n"
            "/users — управление пользователями\n"
            "Поля: time, text, url, weekdays (0=Пн), tracker_tag, start_hour, end_hour, alternate, anchor_date, timezone, schedule, run_at.\n"
            "Чередование: even — группа A в опорную дату, odd — группа B на следующий день. run_at: дата и время с часовым поясом, например 2026-12-01T09:00:00+03:00.\n"
            "Секреты и глобальный запуск меняются в Robopark.",
        )

    def _callback(self, user_id, chat_id, data, context):
        legacy = re.fullmatch(
            r"(tasks|hist|move|mhist|zip|qr):([aа]?\d{1,6})", data, re.IGNORECASE
        )
        if legacy:
            view = {
                "tasks": "open",
                "hist": "history",
                "move": "moves",
                "mhist": "moves_history",
                "zip": "parts",
                "qr": "qr",
            }[legacy[1].lower()]
            robot = parse_robot(legacy[2])
            return (
                self._qr(user_id, chat_id, robot)
                if view == "qr"
                else self._search(user_id, chat_id, robot, view)
            )
        if data.startswith("adm:") and context.get("can_manage"):
            return self._admin_menu(user_id, chat_id)
        parts = data.split(":")
        if len(parts) < 3 or parts[0] != "n":
            return self.say(chat_id, "Это меню устарело. Отправьте /start.")
        action, item = parts[1:3]
        if action == "qr":
            return self._qr(user_id, chat_id, item)
        if action in VIEWS:
            return self._search(user_id, chat_id, item, action)
        if not context.get("can_manage"):
            return self.say(
                chat_id, "Для управления нужен администратор назначенного парка."
            )
        if action == "menu":
            return self._admin_menu(user_id, chat_id, item)
        if action == "users":
            return self._users(user_id, chat_id, item)
        if action == "user":
            self.memberships.pop(user_id, None)
            return self._users(user_id, chat_id, target_id=int(item))
        if action == "revoke":
            if len(parts) != 4:
                raise ValueError("invalid membership action")
            users = self.api.call("GET", f"/manage/users?telegram_user_id={user_id}")
            person = next((u for u in users if u["user_id"] == int(item)), None)
            park = (
                next((p for p in person["parks"] if p["id"] == int(parts[3])), None)
                if person
                else None
            )
            if not park:
                raise ServiceError("park_out_of_scope", status=403)
            if person["role"] in {"admin", "royal"} and context.get("role") != "royal":
                return self.say(chat_id, "Доступ администраторов меняет только royal.")
            nonce = uuid.uuid4().hex[:16]
            self.memberships[user_id] = (time.monotonic() + 300, nonce, person, park)
            self.memberships.move_to_end(user_id)
            while len(self.memberships) > 256:
                self.memberships.popitem(last=False)
            return self.say(
                chat_id,
                f"Убрать доступ сотрудника {person.get('display_name') or person['username']} к парку {park['name']}? Остальные назначения сохранятся.",
                rows=[
                    [("Да, убрать доступ", f"n:revokeconfirm:{nonce}")],
                    [("Отмена", f"n:user:{item}")],
                ],
            )
        if action == "revokeconfirm":
            flow = self.memberships.get(user_id)
            if not flow or flow[0] < time.monotonic() or flow[1] != item:
                return self.say(
                    chat_id, "Подтверждение истекло. Откройте /users заново."
                )
            _, _, person, park = flow
            self.api.call(
                "DELETE",
                f"/manage/users/{person['user_id']}/parks/{park['id']}?telegram_user_id={user_id}",
                {"expected_park_ids": sorted(p["id"] for p in person["parks"])},
            )
            self.memberships.pop(user_id, None)
            return self.say(
                chat_id,
                f"Доступ к парку {park['name']} отозван.",
                rows=[[("← Люди", "n:users:0")]],
            )
        if action == "control":
            if item not in {"status", "pause", "resume"}:
                raise ValueError("invalid control")
            return self._control(user_id, chat_id, "/" + item, "all", context)
        if action == "requests":
            return self._requests(user_id, chat_id, item)
        if action == "select":
            if len(parts) != 5:
                raise ValueError("invalid selection")
            return self._select_access(
                user_id, chat_id, int(item), int(parts[3]), int(parts[4])
            )
        if action in {"greet", "custom"}:
            flow = self.approvals.get(user_id)
            if not flow or flow["nonce"] != item or flow["expires"] < time.monotonic():
                return self.say(chat_id, "Выбор истёк. Откройте /requests заново.")
            if action == "custom":
                flow["custom"] = True
                return self.say(
                    chat_id,
                    "✏️ Отправьте приветствие сотруднику (до 2000 символов). /cancel — отменить.",
                )
            return self._approve_access(user_id, chat_id, flow)
        if action in {"approve", "reject"}:
            if len(parts) != 4:
                raise ValueError("invalid access decision")
            result = self.api.call(
                "POST",
                f"/access/requests/{int(item)}/decision?telegram_user_id={user_id}",
                {"approve": action == "approve", "revision": int(parts[3])},
            )
            self.approvals.pop(user_id, None)
            self.say(
                chat_id,
                f"Заявка №{result['request']['id']}: "
                + ("доступ одобрен." if action == "approve" else "отклонена."),
            )
            recipient = result["request"].get("telegram_user_id")
            if recipient:
                try:
                    self.say(
                        recipient,
                        "✅ Доступ открыт. Отправьте номер робота; /help — помощь."
                        if action == "approve"
                        else "Заявка отклонена администратором. Уточните доступ у администратора своего парка.",
                    )
                except ServiceError:
                    self.say(
                        chat_id,
                        "Решение сохранено, но уведомление сотруднику не доставлено.",
                    )
            return
        if action == "sendconfirm":
            confirmation = self.sends.get(user_id)
            if (
                not confirmation
                or confirmation[0] < time.monotonic()
                or confirmation[1] != item
            ):
                return self.say(
                    chat_id,
                    "Подтверждение отправки истекло. Откройте задание заново через /admin.",
                )
            _, _, job, request_id = confirmation
            self.api.call(
                "POST",
                f"/manage/jobs/{job['id']}/run?telegram_user_id={user_id}",
                {
                    "revision": job["revision"],
                    "request_id": request_id,
                    "allow_disabled": not job["enabled"],
                },
            )
            self.sends.pop(user_id, None)
            return self.say(
                chat_id,
                "Отправка поставлена в очередь. Результат — в журнале Telegram на сайте.",
            )
        if action == "park":
            if len(parts) not in {3, 4}:
                raise ValueError("invalid park page")
            state = self._manage(user_id)
            park = next((p for p in state["parks"] if str(p["id"]) == item), None)
            if park is None:
                raise ServiceError("park_out_of_scope", status=403)
            jobs = [j for j in state["jobs"] if j["park_id"] == park["id"]]
            page = int(parts[3]) if len(parts) == 4 else 0
            page = min(max(0, page), max(0, (len(jobs) - 1) // 20))
            rows = [
                [
                    (
                        ("● " if j["enabled"] else "○ ") + j["title"][:45],
                        f"n:job:{j['id']}",
                    )
                ]
                for j in jobs[page * 20 : (page + 1) * 20]
            ]
            navigation = []
            if page:
                navigation.append(("← Назад", f"n:park:{park['id']}:{page - 1}"))
            if (page + 1) * 20 < len(jobs):
                navigation.append(("Далее →", f"n:park:{park['id']}:{page + 1}"))
            if navigation:
                rows.append(navigation)
            rows += [
                [("➕ Создать рассылку", f"n:newmenu:{park['id']}")],
                [("← Локации", "n:menu:parks")],
            ]
            return self.say(
                chat_id,
                f"{park['name']} · {park['tag']}\nID парка: {park['id']}\nЧат: {park.get('chat_id') or 'не задан'}, тема: {park.get('thread_id') or 'общая'}\nЗаданий: {len(jobs)}",
                rows=rows,
            )
        if action == "newmenu":
            return self.say(
                chat_id,
                "Что создать? Задание появится выключенным — сначала заполните настройки.",
                rows=[
                    [
                        ("📤 PNG", f"n:newjob:{item}:report"),
                        ("📢 Текст", f"n:newjob:{item}:text"),
                    ],
                    [
                        ("🍩 СК", f"n:newjob:{item}:campaign"),
                        ("📹 Zoom", f"n:newjob:{item}:zoom"),
                    ],
                    [("← Назад", f"n:park:{item}")],
                ],
            )
        if action == "newjob":
            if len(parts) != 4 or parts[3] not in {
                "report",
                "text",
                "campaign",
                "zoom",
            }:
                raise ValueError("invalid job kind")
            titles = {
                "report": "PNG отчёт",
                "text": "Объявление",
                "campaign": "СК",
                "zoom": "Созвон",
            }
            return self._admin_command(
                user_id, chat_id, f"/new {int(item)} {parts[3]} {titles[parts[3]]}"
            )
        if action == "job":
            return self._show_job(user_id, chat_id, item)
        job = self._job(user_id, item)
        if len(parts) != 4 or str(job["revision"]) != parts[3]:
            return self.say(chat_id, "Настройки изменились. Откройте /admin заново.")
        if action == "cfg":
            return self._show_job_settings(chat_id, job)
        edit_fields = {
            "title": "title",
            "url": "url",
            "days": "weekdays",
            "zone": "timezone",
            "alt": "alternate",
            "sched": "schedule",
            "tag": "tracker_tag",
            "time": "time",
            "text": "text",
        }
        if action in edit_fields:
            return self._begin_job_edit(user_id, chat_id, job, edit_fields[action])
        if action == "toggle":
            job["enabled"] = not job["enabled"]
            self._save_job(user_id, job)
            return self._show_job(user_id, chat_id, item)
        if action == "send":
            request_id = str(uuid.uuid4())
            nonce = uuid.uuid4().hex[:16]
            self.sends[user_id] = (time.monotonic() + 300, nonce, job, request_id)
            self.sends.move_to_end(user_id)
            while len(self.sends) > 500:
                self.sends.popitem(last=False)
            return self.say(
                chat_id,
                f"Отправить «{job['title']}» в настроенный чат парка сейчас? Расписание не изменится.",
                rows=[[("Подтвердить отправку", f"n:sendconfirm:{nonce}")]],
            )
        if action == "delete":
            return self.say(
                chat_id,
                f"Удалить задание «{job['title']}»?",
                rows=[[("Да, удалить", f"n:confirm:{item}:{job['revision']}")]],
            )
        if action == "confirm":
            self.api.call(
                "DELETE",
                f"/manage/jobs/{item}?"
                + urlencode({"telegram_user_id": user_id, "revision": job["revision"]}),
            )
            return self.say(chat_id, "Задание удалено.")

    def _admin_command(self, user_id, chat_id, text):
        command, _, rest = text.partition(" ")
        if command in {"/admin", "/jobs"}:
            return self._admin_menu(user_id, chat_id)
        if command == "/chat":
            args = rest.split()
            if not 2 <= len(args) <= 3:
                return self.say(
                    chat_id,
                    "Формат: /chat ПАРК CHAT_ID [THREAD_ID]. Для удаления чата: /chat ПАРК none",
                )
            state = self._manage(user_id)
            park = next((p for p in state["parks"] if str(p["id"]) == args[0]), None)
            if park is None:
                raise ServiceError("park_out_of_scope", status=403)
            chat = None if args[1] == "none" else int(args[1])
            thread = int(args[2]) if len(args) == 3 else None
            self.api.call(
                "PUT",
                f"/manage/parks/{park['id']}?telegram_user_id={user_id}",
                {"chat_id": chat, "thread_id": thread, "revision": park["revision"]},
            )
            return self.say(chat_id, "Чат и тема сохранены в Robopark.")
        if command == "/new":
            args = rest.split(" ", 2)
            if len(args) != 3 or args[1] not in {"text", "zoom", "report", "campaign"}:
                return self.say(
                    chat_id, "Формат: /new ПАРК text|zoom|report|campaign НАЗВАНИЕ"
                )
            # Disabled draft has a usable schedule; required message/link/tag are edited next.
            job = self.api.call(
                "POST",
                f"/manage/jobs?telegram_user_id={user_id}",
                {
                    "park_id": int(args[0]),
                    "kind": args[1],
                    "title": args[2],
                    "enabled": False,
                    "schedule": "daily",
                    "timezone": "Europe/Moscow" if args[1] == "zoom" else None,
                    "time": "09:00",
                    "run_at": None,
                    "weekdays": list(range(7)),
                    "start_hour": None,
                    "end_hour": None,
                    "text": None,
                    "url": None,
                    "tracker_tag": None,
                    "alternate": "all",
                    "anchor_date": None,
                },
            )
            return self._show_job(user_id, chat_id, job["id"])
        if command == "/set":
            args = rest.split(" ", 2)
            allowed = {
                "time",
                "text",
                "url",
                "weekdays",
                "tracker_tag",
                "start_hour",
                "end_hour",
                "alternate",
                "anchor_date",
                "schedule",
                "title",
                "timezone",
                "run_at",
            }
            if len(args) != 3 or args[1] not in allowed:
                return self.say(
                    chat_id, "Формат: /set ID поле значение. Список полей — /admin."
                )
            job = self._job(user_id, args[0])
            field, value = args[1:]
            job = _apply_job_edit(job, field, value)
            self._save_job(user_id, job)
            return self._show_job(user_id, chat_id, job["id"])

    def handle_update(self, update):
        callback = update.get("callback_query")
        message = callback.get("message", {}) if callback else update.get("message", {})
        sender = callback.get("from", {}) if callback else message.get("from", {})
        chat = message.get("chat", {})
        user_id, chat_id = sender.get("id"), chat.get("id")
        if (
            not isinstance(user_id, int)
            or not isinstance(chat_id, int)
            or sender.get("is_bot")
        ):
            return
        if callback:
            try:
                self.telegram.call(
                    "answerCallbackQuery", {"callback_query_id": callback["id"]}
                )
            except ServiceError:
                pass
        text = str(message.get("text") or "").strip()
        text = {
            "⚙️ Управление": "/admin",
            "📊 Статус": "/status",
            "ℹ️ Помощь": "/help",
            "🔍 Робот": "/robot",
            "⏸ Пауза": "/pause all",
            "▶️ Старт": "/resume all",
        }.get(text, text)
        if chat.get("type") != "private":
            # Group replies must not expose another user's scoped task data or link codes.
            if text.split("@")[0] == "/where":
                self.say(
                    chat_id,
                    f"Chat ID: {chat_id}\nThread ID: {message.get('message_thread_id') or 'нет'}",
                )
            return
        if callback or text.startswith("/"):
            # Leaving the input flow must not treat a later robot number as a job edit.
            self.pending.pop(user_id, None)
        try:
            if callback and str(callback.get("data") or "").startswith(
                ("n:join:", "n:apply:")
            ):
                return self._join_callback(
                    user_id, chat_id, str(callback["data"]), sender
                )
            if not callback and text in {"/register", "/join"}:
                return self._join(chat_id)
            if text.startswith("/link ") and not callback:
                code = text.split(maxsplit=1)[1]
                if len(code) > 128:
                    raise ValueError("code too long")
                self.api.call(
                    "POST", "/link", {"code": code, "telegram_user_id": user_id}
                )
                return self.say(
                    chat_id,
                    "Telegram подключён к Robopark. /access — доступ и заявка в парк; номер робота — поиск задач; /admin — управление.",
                )
            if not callback and text in {"/start", "/access"}:
                return self._access(user_id, chat_id)
            if callback and str(callback.get("data") or "").startswith("n:request:"):
                park_id = int(callback["data"].split(":")[2])
                self.api.call(
                    "POST",
                    "/access/requests",
                    {"telegram_user_id": user_id, "park_id": park_id},
                )
                self.say(
                    chat_id,
                    "Заявка отправлена. Статус можно проверить командой /access.",
                )
                return self._access(user_id, chat_id)
            context = self.api.call("GET", f"/context?telegram_user_id={user_id}")
            if callback:
                return self._callback(
                    user_id, chat_id, str(callback.get("data") or ""), context
                )
            if text == "/help":
                return self._help(chat_id, context)
            if text == "/robot":
                return self.say(
                    chat_id,
                    "🔍 Отправьте номер робота, например 1460 или a1460.",
                    reply=_reply_keyboard(context),
                )
            if text == "/commands" and context.get("can_manage"):
                return self._commands(chat_id)
            command, _, argument = text.partition(" ")
            if command == "/qr":
                robot = parse_robot(argument)
                if not robot:
                    raise ValueError("invalid robot")
                return self._qr(user_id, chat_id, robot)
            if command == "/stats":
                if argument and (argument != "all" or not context.get("can_manage")):
                    raise ServiceError("permission_denied", status=403)
                return self._stats(user_id, chat_id, all_users=argument == "all")
            if command in {"/pause", "/resume", "/status", "/users"}:
                if not context.get("can_manage"):
                    raise ServiceError("permission_denied", status=403)
                if command == "/users":
                    return self._users(user_id, chat_id)
                return self._control(user_id, chat_id, command, argument, context)
            if text == "/cancel":
                self.pending.pop(user_id, None)
                self.sends.pop(user_id, None)
                self.approvals.pop(user_id, None)
                self.memberships.pop(user_id, None)
                return self.say(chat_id, "Ввод отменён.")
            if text in {"/requests", "/approvals"}:
                if not context.get("can_manage"):
                    raise ServiceError("permission_denied", status=403)
                return self._requests(user_id, chat_id)
            approval = self.approvals.get(user_id)
            if approval and approval["custom"] and not text.startswith("/"):
                if not context.get("can_manage"):
                    raise ServiceError("permission_denied", status=403)
                if approval["expires"] < time.monotonic():
                    self.approvals.pop(user_id, None)
                    return self.say(chat_id, "Выбор истёк. Откройте /requests заново.")
                if not text or len(text) > 2000:
                    return self.say(
                        chat_id,
                        "Приветствие должно быть от 1 до 2000 символов. /cancel — отменить.",
                    )
                return self._approve_access(user_id, chat_id, approval, text)
            pending = self.pending.get(user_id)
            if pending and not text.startswith("/"):
                if not context.get("can_manage"):
                    raise ServiceError("permission_denied", status=403)
                if pending["expires"] < time.monotonic():
                    self.pending.pop(user_id, None)
                    return self.say(
                        chat_id, "Время ввода истекло. Откройте задание заново."
                    )
                job = self._job(user_id, pending["job_id"])
                if job["revision"] != pending["revision"]:
                    self.pending.pop(user_id, None)
                    return self.say(
                        chat_id,
                        "Настройки изменились. Откройте задание заново.",
                    )
                try:
                    candidate = _apply_job_edit(job, pending["field"], text)
                except (TypeError, ValueError):
                    return self.say(
                        chat_id,
                        "Неверный формат. Исправьте значение или /cancel.",
                    )
                try:
                    self._save_job(user_id, candidate)
                except ServiceError as error:
                    if error.status == 422:
                        return self.say(
                            chat_id,
                            "Сервер не принял значение. Исправьте его или /cancel.",
                        )
                    raise
                self.pending.pop(user_id, None)
                return self._show_job(user_id, chat_id, job["id"])
            if text.split(" ", 1)[0] in {"/admin", "/jobs", "/chat", "/set", "/new"}:
                if not context.get("can_manage"):
                    return self.say(
                        chat_id,
                        "Для управления нужен администратор назначенного парка.",
                    )
                return self._admin_command(user_id, chat_id, text)
            command, _, argument = text.partition(" ")
            view = {
                "/history": "history",
                "/moves": "moves",
                "/moves_history": "moves_history",
                "/parts": "parts",
            }.get(command, "open")
            robot = parse_robot(argument if view != "open" else text)
            if robot:
                return self._search(user_id, chat_id, robot, view)
            self._help(chat_id, context)
        except ValueError:
            self.say(
                chat_id, "Неверный формат. Проверьте номер, ID или время и повторите."
            )
        except ServiceError as error:
            if error.code in {
                "bot_tracker_queue_forbidden",
                "native_auxiliary_queue_disabled",
            }:
                text = "Эта очередь пока не подключена к боту. Обратитесь к royal в Robopark."
            elif error.code in {"invalid_link_code", "link_code_expired"}:
                text = "Код подключения неверен или истёк. Получите новый код в Профиль → Telegram в Robopark."
            elif error.code == "link_throttled":
                text = "Слишком много попыток подключения. Повторите через 10 минут."
            elif error.code in {"user_already_linked", "telegram_id_already_bound"}:
                text = "Аккаунт уже связан. Сначала отключите старую привязку в Профиль → Telegram в Robopark."
            elif error.code == "native_queries_paused":
                text = "Запросы временно на паузе. Администратор может возобновить их в Robopark или Telegram."
            elif error.code == "request_already_resolved":
                text = "Заявку уже обработал другой администратор. /requests — обновить список."
            elif error.status == 403:
                return self._join(chat_id)
            elif error.status == 401:
                text = "Не удалось проверить доступ. Повторите позже или обратитесь к администратору."
            elif error.status in {409, 412}:
                text = "Настройки изменились или код уже использован. Обновите данные в Robopark или откройте /admin заново."
            elif error.status == 422:
                text = "Настройки не приняты. Проверьте поля; перед включением заполните текст, ссылку или тег кампании."
            else:
                text = "Сервис временно недоступен. Повторите запрос позже."
            self.say(chat_id, text)

    def deliver(self, item):
        base = f"/deliveries/{item['id']}"
        lease = {"lease_token": item["lease_token"]}
        job, park = item["job"], item["park"]
        started, sent_parts = False, 0
        try:
            text = (job.get("text") or job["title"]).replace(
                "{link}", job.get("url") or ""
            )
            if job.get("url") and job["url"] not in text:
                text += "\n" + job["url"]
            parts = []
            if job["kind"] in {"report", "campaign"}:
                content = self.api.call("POST", base + "/content", lease)
                issues = content["issues"]
                if len(issues) > 500:
                    raise ServiceError("report_response_too_large")
                text = f"{park['name']} · {job['title']}" + (
                    "\n" + text if text != job["title"] else ""
                )
                if content.get("truncated"):
                    text += "\nВыборка ограничена; полный список — в Robopark."
                if job["kind"] == "campaign":
                    parts.append(
                        (
                            "sendPhoto",
                            {"caption": text},
                            render_campaign(
                                job,
                                park,
                                issues,
                                truncated=content.get("truncated", False),
                            ),
                        )
                    )
                else:
                    pages = report_page_count(issues)
                    for page in range(pages):
                        caption = text + (f" · {page + 1}/{pages}" if pages > 1 else "")
                        parts.append(
                            (
                                "sendPhoto",
                                {"caption": caption},
                                render_report(
                                    job,
                                    park,
                                    issues,
                                    truncated=content.get("truncated", False),
                                    report_summary=content.get("report_summary"),
                                    page=page,
                                ),
                            )
                        )
                    for part in watchdog_parts(issues):
                        parts.append(
                            (
                                "sendMessage",
                                {
                                    "text": part,
                                    "parse_mode": "HTML",
                                    "link_preview_options": {"is_disabled": True},
                                },
                                None,
                            )
                        )
            else:
                parts.append(("sendMessage", {"text": text}, None))
            if any(
                len(body.get("caption", body.get("text", "")))
                > (1024 if photo is not None else 4096)
                for _, body, photo in parts
            ):
                raise ServiceError("message_too_long")
            ready = self.api.call("POST", base + "/begin", lease)
            if ready.get("ready") is not True:
                raise ServiceError("delivery_not_ready", status=409)
            started = True
            payload = {"chat_id": park["chat_id"]}
            if park.get("thread_id"):
                payload["message_thread_id"] = park["thread_id"]
            for method, body, photo in parts:
                if photo is None:
                    self.telegram.call(method, {**payload, **body})
                else:
                    self.telegram.call(method, {**payload, **body}, photo=photo)
                sent_parts += 1
        except Exception as error:  # noqa: BLE001 - preserve an uncertain delivery receipt for any failure
            if (
                isinstance(error, ServiceError)
                and error.code
                in {
                    "delivery_not_ready",
                    "delivery_cancelled",
                    "delivery_lease_invalid",
                    "native_deliveries_paused",
                }
                and not started
            ):
                return
            state = (
                "unknown"
                if started and (not isinstance(error, ServiceError) or error.uncertain)
                else "failed"
            )
            code = (
                error.code
                if isinstance(error, ServiceError)
                else ("delivery_failed" if started else "report_preparation_failed")
            )
            if sent_parts:
                code = f"partial_{sent_parts}_{code}"[:80]
            self._finish(base, lease, state, code)
            return
        self._finish(base, lease, "sent", None)

    def _finish(self, base, lease, state, code):
        try:
            self.api.call(
                "POST", base + "/finish", {**lease, "state": state, "error_code": code}
            )
        except ServiceError:
            # The server retains the sending receipt; a missing ACK must not resend.
            print("bot delivery acknowledgement unavailable", flush=True)
