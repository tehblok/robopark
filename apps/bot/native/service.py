from __future__ import annotations

import re
from html import unescape
import time
import uuid
from collections import OrderedDict
from urllib.parse import urlencode

from native.campaigns import render_campaign
from native.qr import render_robot_qr, yasadr_code
from native.reports import render_report, report_page_count, status_label, watchdog_parts
from native.transport import ServiceError

ROBOT = re.compile(r"^(?:/robot\s+)?[aа]?(\d{1,6})$", re.IGNORECASE)
JOB_ID = re.compile(r"^[a-f0-9-]{36}$")
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,63}-[1-9][0-9]{0,18}$")
VIEWS = {
    "open": "Открытые задачи",
    "history": "История ремонтов",
    "moves": "Перемещения",
    "moves_history": "История перемещений",
    "parts": "Поставка запчастей",
}


def parse_robot(value):
    hit = ROBOT.fullmatch(value.strip())
    return f"a{hit.group(1)}" if hit else None


def format_issues(robot, issues, *, history=False, view=None, truncated=False):
    view = view or ("history" if history else "open")
    title = f"{robot} · " + VIEWS[view]
    if not issues:
        return title + "\nПо вашим паркам задачи не найдены."
    blocks, size = [title], len(title)
    for issue in issues[:20]:
        key = str(issue.get("key") or "")
        summary = str(issue.get("summary") or "").replace("\n", " ")[:180]
        block = f"{key} · {status_label(issue)}\n{summary}"
        if ISSUE_KEY.fullmatch(key):
            block += f"\nhttps://st.yandex-team.ru/{key}"
        if view in {"history", "moves_history"} and issue.get("resolvedAt"):
            block += "\nЗакрыта: " + str(issue["resolvedAt"])[:10]
        if size + len(block) > 3600:
            truncated = True
            break
        blocks.append(block)
        size += len(block) + 2
    if truncated or len(issues) > len(blocks) - 1:
        blocks.append("Список ограничен. Полная выборка доступна в Robopark.")
    return "\n\n".join(blocks)


def _keyboard(rows):
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in rows
        ]
    }


class BotService:
    def __init__(self, api, telegram):
        self.api, self.telegram = api, telegram
        self.pending = OrderedDict()
        self.sends = OrderedDict()

    def say(self, chat_id, text, *, rows=None):
        payload = {
            "chat_id": chat_id,
            "text": text[:3900],
            "link_preview_options": {"is_disabled": True},
        }
        if rows:
            payload["reply_markup"] = _keyboard(rows)
        return self.telegram.call("sendMessage", payload)

    def _manage(self, user_id):
        return self.api.call("GET", f"/manage?telegram_user_id={user_id}")

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
        return self.say(chat_id, "\n".join(lines), rows=rows)

    def _requests(self, user_id, chat_id, page=0):
        data = self.api.call("GET", f"/access/requests?telegram_user_id={user_id}")
        page = max(0, min(int(page), max(0, (len(data) - 1) // 8)))
        rows, lines = [], ["Заявки на доступ к вашим паркам"]
        for item in data[page * 8 : (page + 1) * 8]:
            role = {"mechanic": "Механик", "operator": "Оператор"}.get(
                item["role"], item["role"]
            )
            lines.append(
                f"№{item['id']} · {item['username'][:80]} · {role}\nПарк: {item['park']['name'][:100]}"
            )
            suffix = f"{item['id']}:{item['revision']}"
            rows.append(
                [
                    (f"Одобрить №{item['id']}", f"n:approve:{suffix}"),
                    (f"Отклонить №{item['id']}", f"n:reject:{suffix}"),
                ]
            )
        if not data:
            lines.append("Новых заявок нет.")
        navigation = []
        if page:
            navigation.append(("← Назад", f"n:requests:{page - 1}"))
        if (page + 1) * 8 < len(data):
            navigation.append(("Далее →", f"n:requests:{page + 1}"))
        if navigation:
            rows.append(navigation)
        rows.append([("Обновить", "n:requests:0")])
        return self.say(chat_id, "\n\n".join(lines), rows=rows)

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

    def _show_job(self, user_id, chat_id, job_id):
        job = self._job(user_id, job_id)
        prefix = f"{job['id']}:{job['revision']}"
        self.say(
            chat_id,
            f"{job['title']}\n{'Включено' if job['enabled'] else 'Выключено'} · {job['schedule']} · {job.get('time') or str(job.get('start_hour')) + '–' + str(job.get('end_hour'))}\n"
            f"{job.get('text') or ''}\n{job.get('url') or ''}\nID: {job['id']}",
            rows=[
                [("Выключить" if job["enabled"] else "Включить", f"n:toggle:{prefix}")],
                [
                    ("Изменить время", f"n:time:{prefix}"),
                    ("Изменить текст", f"n:text:{prefix}"),
                ],
                [("Удалить", f"n:delete:{prefix}")],
                [("Отправить сейчас", f"n:send:{prefix}")],
            ],
        )

    def _search(self, user_id, chat_id, robot, view):
        if parse_robot(robot) is None or view not in VIEWS:
            return self.say(chat_id, "Отправьте точный номер робота, например 1460.")
        data = self.api.call(
            "GET", f"/robots/{robot}?telegram_user_id={user_id}&view={view}"
        )
        if data.get("greeting"):
            self.say(chat_id, unescape(data["greeting"]))
        return self.say(
            chat_id,
            format_issues(
                robot, data["issues"], view=view, truncated=data.get("truncated", False)
            ),
            rows=[
                [
                    ("Открытые задачи", f"n:open:{robot}"),
                    ("История ремонтов", f"n:history:{robot}"),
                ],
                [
                    ("Перемещения", f"n:moves:{robot}"),
                    ("История перемещений", f"n:moves_history:{robot}"),
                ],
                [("Запчасти", f"n:parts:{robot}"), ("QR-код", f"n:qr:{robot}")],
            ],
        )

    def _qr(self, user_id, chat_id, robot):
        code = yasadr_code(robot)
        # Same approval, park permissions and pause checks as other robot commands.
        data = self.api.call("GET", f"/robots/{parse_robot(robot)}?telegram_user_id={user_id}&view=open")
        if not data.get("issues"):
            return self.say(chat_id, "Робот не найден в доступных вам задачах. QR-код недоступен.")
        return self.telegram.call("sendPhoto", {"chat_id": chat_id, "caption": code}, photo=render_robot_qr(robot))

    def _stats(self, user_id, chat_id, *, all_users=False):
        data = self.api.call("GET", f"/usage{'/all' if all_users else ''}?telegram_user_id={user_id}")
        records = data if all_users else [data]
        lines = ["Запросы: сегодня / месяц / всего"]
        for row in records:
            stats = row['stats']
            lines.append(f"{row['username'][:80]}: {stats['today']} / {stats['month']} / {stats['total']}")
        # Bounded messages retain every visible user rather than truncating the list.
        block = []
        for line in lines:
            if sum(len(x) + 1 for x in block) + len(line) > 3700:
                self.say(chat_id, "\n".join(block))
                block = []
            block.append(line)
        return self.say(chat_id, "\n".join(block))

    def _control(self, user_id, chat_id, command, argument, context):
        if command != "/status" and context.get("role") != "royal":
            return self.say(chat_id, "Общую паузу сервиса меняет только royal.")
        if command != "/status" and argument not in {"queries", "deliveries", "all"}:
            return self.say(chat_id, "Формат: /pause или /resume, затем queries (запросы), deliveries (рассылки) или all.")
        path = f"/control?telegram_user_id={user_id}"
        state = self.api.call("GET", path)
        if command != "/status":
            for scope in ("queries", "deliveries"):
                if argument in {scope, "all"}:
                    state[scope + "_paused"] = command == "/pause"
            state = self.api.call("PUT", path, state)
        return self.say(chat_id, "Запросы сотрудников: " + ("пауза" if state['queries_paused'] else "работают") + "\nРассылки: " + ("пауза" if state['deliveries_paused'] else "работают") + "\nАдминистраторы могут проверять запросы и управлять ботом во время паузы.")

    def _admin_menu(self, user_id, chat_id):
        state = self._manage(user_id)
        rows = [
            [(park["name"][:50], f"n:park:{park['id']}")]
            for park in state["parks"][:40]
        ]
        self.say(
            chat_id,
            "Управление Telegram · выберите парк.\n"
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
            rows=rows,
        )

    def _callback(self, user_id, chat_id, data, context):
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
        if action == "requests":
            return self._requests(user_id, chat_id, item)
        if action in {"approve", "reject"}:
            if len(parts) != 4:
                raise ValueError("invalid access decision")
            result = self.api.call(
                "POST",
                f"/access/requests/{int(item)}/decision?telegram_user_id={user_id}",
                {"approve": action == "approve", "revision": int(parts[3])},
            )
            self.say(
                chat_id,
                f"Заявка №{result['request']['id']}: "
                + ("доступ одобрен." if action == "approve" else "отклонена."),
            )
            return self._requests(user_id, chat_id)
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
            state = self._manage(user_id)
            park = next((p for p in state["parks"] if str(p["id"]) == item), None)
            if park is None:
                raise ServiceError("park_out_of_scope", status=403)
            jobs = [j for j in state["jobs"] if j["park_id"] == park["id"]]
            rows = [
                [
                    (
                        ("● " if j["enabled"] else "○ ") + j["title"][:45],
                        f"n:job:{j['id']}",
                    )
                ]
                for j in jobs[:40]
            ]
            return self.say(
                chat_id,
                f"{park['name']} · {park['tag']}\nID парка: {park['id']}\nЧат: {park.get('chat_id') or 'не задан'}, тема: {park.get('thread_id') or 'общая'}\nЗаданий: {len(jobs)}",
                rows=rows,
            )
        if action == "job":
            return self._show_job(user_id, chat_id, item)
        job = self._job(user_id, item)
        if len(parts) != 4 or str(job["revision"]) != parts[3]:
            return self.say(chat_id, "Настройки изменились. Откройте /admin заново.")
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
        if action in {"time", "text"}:
            if action == "time" and job["schedule"] == "hourly":
                return self.say(
                    chat_id,
                    f"Почасовое расписание: /set {item} start_hour ЧАС и /set {item} end_hour ЧАС. Или измените расписание в Robopark.",
                )
            self.pending[user_id] = (time.monotonic() + 300, job, action)
            self.pending.move_to_end(user_id)
            while len(self.pending) > 500:
                self.pending.popitem(last=False)
            return self.say(
                chat_id,
                "Отправьте новое время ЧЧ:ММ."
                if action == "time"
                else "Отправьте новый текст. Для отмены — /cancel.",
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
                    "time": "09:00",
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
            if field == "weekdays":
                value = [int(x.strip()) for x in value.split(",")]
            elif field in {"start_hour", "end_hour"}:
                value = int(value)
            elif value == "none":
                value = None
            job[field] = value
            if field == "run_at" and value:
                job.update(
                    schedule="once",
                    time=None,
                    start_hour=None,
                    end_hour=None,
                    weekdays=[],
                    alternate="all",
                    anchor_date=None,
                )
            if field == "schedule":
                if value == "once":
                    job.update(
                        time=None,
                        start_hour=None,
                        end_hour=None,
                        weekdays=[],
                        alternate="all",
                        anchor_date=None,
                    )
                    if not job.get("run_at"):
                        return self.say(
                            chat_id,
                            "Для разового задания сначала задайте дату на сайте. Затем время можно менять через /set ID run_at ДАТА_С_ПОЯСОМ.",
                        )
                elif value in {"daily", "hourly"}:
                    job["run_at"] = None
                    if not job["weekdays"]:
                        job["weekdays"] = list(range(7))
                    if value == "daily":
                        job.update(
                            time=job.get("time") or "09:00",
                            start_hour=None,
                            end_hour=None,
                        )
                    else:
                        job.update(
                            time=None,
                            start_hour=job.get("start_hour")
                            if job.get("start_hour") is not None
                            else 9,
                            end_hour=job.get("end_hour")
                            if job.get("end_hour") is not None
                            else 21,
                        )
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
        if chat.get("type") != "private":
            # Group replies must not expose another user's scoped task data or link codes.
            if text.split("@")[0] == "/where":
                self.say(
                    chat_id,
                    f"Chat ID: {chat_id}\nThread ID: {message.get('message_thread_id') or 'нет'}",
                )
            return
        try:
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
                    return self.say(chat_id, "Заявки на доступ: /requests. Роли, список пользователей и отключение аккаунтов: Robopark → Администрирование → Пользователи. Изменения сразу действуют в боте.")
                return self._control(user_id, chat_id, command, argument, context)
            if text == "/cancel":
                self.pending.pop(user_id, None)
                self.sends.pop(user_id, None)
                return self.say(chat_id, "Ввод отменён.")
            if text in {"/requests", "/approvals"}:
                if not context.get("can_manage"):
                    raise ServiceError("permission_denied", status=403)
                return self._requests(user_id, chat_id)
            pending = self.pending.pop(user_id, None)
            if pending and not text.startswith("/") and pending[0] >= time.monotonic():
                if not context.get("can_manage"):
                    raise ServiceError("permission_denied", status=403)
                _, job, field = pending
                job[field] = text
                self._save_job(user_id, job)
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
            self.say(
                chat_id,
                "Robopark · отправьте номер робота — покажу задачи ваших парков.\n"
                "/history НОМЕР — история ремонтов\n/moves НОМЕР — перемещения\n/parts НОМЕР — запчасти\n/qr НОМЕР — QR-код YASADR\n/stats — мои запросы\n/admin — управление рассылками\n"
                "/requests — заявки на доступ (для админов)\n/access — мой доступ и парки\n"
                "/where в группе — узнать чат и тему для настройки.",
            )
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
            elif error.status in {401, 403}:
                text = "Доступ не подтверждён. Войдите или зарегистрируйтесь в Robopark, получите код привязки Telegram и отправьте /link КОД. Если уже связали аккаунт — /access для заявки в парк."
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
            text = (job.get("text") or job["title"]).replace("{link}", job.get("url") or "")
            if job.get("url") and job["url"] not in text:
                text += "\n" + job["url"]
            parts = []
            if job["kind"] in {"report", "campaign"}:
                content = self.api.call("POST", base + "/content", lease)
                issues = content["issues"]
                if len(issues) > 500:
                    raise ServiceError("report_response_too_large")
                text = f"{park['name']} · {job['title']}" + ("\n" + text if text != job["title"] else "")
                if content.get("truncated"):
                    text += "\nВыборка ограничена; полный список — в Robopark."
                if job["kind"] == "campaign":
                    parts.append(("sendPhoto", {"caption": text}, render_campaign(job, park, issues, truncated=content.get("truncated", False))))
                else:
                    pages = report_page_count(issues)
                    for page in range(pages):
                        caption = text + (f" · {page + 1}/{pages}" if pages > 1 else "")
                        parts.append(("sendPhoto", {"caption": caption}, render_report(job, park, issues, truncated=content.get("truncated", False), report_summary=content.get("report_summary"), page=page)))
                    for part in watchdog_parts(issues):
                        parts.append(("sendMessage", {"text": part, "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}, None))
            else:
                parts.append(("sendMessage", {"text": text}, None))
            if any(len(body.get("caption", body.get("text", ""))) > (1024 if photo is not None else 4096) for _, body, photo in parts):
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
