from __future__ import annotations

import sys
from pathlib import Path

import pytest

BOT = Path(__file__).resolve().parents[2] / "apps/bot"
sys.path.insert(0, str(BOT))
from native.reports import render_report
from native.runtime import (
    UpdateWorkers,
    acquire_instance,
    health_snapshot,
    read_offset,
    save_offset,
)
from native.service import (
    BotService,
    ServiceError,
    format_issues,
    parse_robot,
)


class API:
    def __init__(self):
        self.calls = []
        self.fail_begin = False

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.endswith("/begin") and self.fail_begin:
            raise ServiceError("delivery_cancelled", status=409)
        if "/context?" in path:
            return {
                "role": "mechanic",
                "can_manage": False,
                "name": "Test",
                "parks": [],
            }
        if "/robots/" in path:
            return {"robot": "a1460", "issues": [], "truncated": False}
        return {"ready": True, "recorded": True}


class Telegram:
    def __init__(self):
        self.calls = []
        self.fail_send = False

    def call(self, method, payload=None, **kwargs):
        self.calls.append((method, payload, kwargs))
        if method == "sendMessage" and self.fail_send:
            raise ServiceError("telegram_transport_error", uncertain=True)
        return {"message_id": 123}


def message(text, *, user_id=7, chat_type="private"):
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "text": text,
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": chat_type},
        },
    }


def delivery():
    return {
        "id": 11,
        "lease_token": "test-lease",
        "job": {
            "kind": "zoom",
            "title": "Встреча",
            "text": "Встреча {link} {обычный текст}",
            "url": "https://zoom.example/meeting",
        },
        "park": {"id": 1, "name": "Парк", "chat_id": -1001234567890, "thread_id": 12},
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1460", "a1460"),
        ("А01460", "a01460"),
        ("/robot 1460", "a1460"),
        ("1460 OR *", None),
    ],
)
def test_robot_input_is_bounded_exact_number(value, expected):
    assert parse_robot(value) == expected


def test_link_is_only_accepted_in_private_chat():
    api, tg = API(), Telegram()
    service = BotService(api, tg)
    service.handle_update(message("/link hidden-code", chat_type="supergroup"))
    assert not api.calls
    assert "hidden-code" not in str(tg.calls)


def test_robot_search_uses_authenticated_sender_and_server_scope():
    api, tg = API(), Telegram()
    BotService(api, tg).handle_update(message("1460"))
    assert any(
        "/robots/a1460?telegram_user_id=7&view=open" == p for _, p, _ in api.calls
    )
    assert tg.calls[-1][1]["chat_id"] == 7


def test_admin_operation_checks_role_before_modification():
    api, tg = API(), Telegram()
    BotService(api, tg).handle_update(message("/chat 1 -1001234567890 12"))
    assert all(method == "GET" for method, _, _ in api.calls)
    assert "администратор" in tg.calls[-1][1]["text"].lower()


def callback(data, *, user_id=7, chat_type="private"):
    return {
        "callback_query": {
            "id": "cb",
            "data": data,
            "from": {"id": user_id},
            "message": {"chat": {"id": user_id, "type": chat_type}},
        }
    }


class AccessAPI(API):
    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        request = {
            "id": 10,
            "revision": 1,
            "username": "Новый механик",
            "role": "mechanic",
            "park": {"id": 3, "name": "Северный"},
            "status": "pending",
        }
        if path.startswith("/access?"):
            return {
                "access_status": "pending",
                "assigned_parks": [],
                "available_parks": [{"id": 3, "name": "Северный"}],
                "requests": [],
            }
        if path.startswith("/access/requests?"):
            return [request]
        if "/decision?" in path:
            return {"request": request, "user_access_status": "approved"}
        if path == "/access/requests":
            return request
        if path.startswith("/context?"):
            return {"can_manage": True, "role": "admin"}
        if path.startswith("/manage?"):
            return {"parks": [{"id": 3, "name": "Северный"}], "jobs": []}
        if path == "/onboarding/parks":
            return {"parks": [{"id": 3, "name": "Северный"}]}
        return super().call(method, path, payload)


class OnboardingAPI(API):
    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.startswith(("/access?", "/context?")):
            raise ServiceError("permission_denied", status=403)
        if path == "/onboarding/parks":
            return {"parks": [{"id": 3, "name": "Северный"}]}
        if path == "/onboarding/request":
            return {"state": "pending", "role": payload["role"], "park_id": 3}
        raise AssertionError(path)


def test_unknown_employee_requests_access_entirely_in_telegram():
    api, tg = OnboardingAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(message("/start"))
    buttons = tg.calls[-1][1]["reply_markup"]["inline_keyboard"]
    assert {b["callback_data"] for row in buttons for b in row} == {
        "n:join:mechanic:0",
        "n:join:operator:0",
    }
    assert not any(method == "POST" for method, _, _ in api.calls)
    service.handle_update(callback("n:join:mechanic:0"))
    assert (
        tg.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        == "n:apply:mechanic:3"
    )
    service.handle_update(callback("n:apply:mechanic:3", user_id=42))
    assert (
        "POST",
        "/onboarding/request",
        {"telegram_user_id": 42, "role": "mechanic", "park_id": 3},
    ) in api.calls
    assert "Заявка отправлена" in tg.calls[-1][1]["text"]
    assert "/link" not in tg.calls[-1][1]["text"]


@pytest.mark.parametrize(
    "data",
    ["n:apply:royal:3", "n:apply:admin:3", "n:join:admin:0", "n:apply:operator:-1"],
)
def test_onboarding_rejects_forged_privileged_role_or_park(data):
    api, tg = OnboardingAPI(), Telegram()
    BotService(api, tg).handle_update(callback(data))
    assert not any(method == "POST" for method, _, _ in api.calls)


def test_onboarding_never_runs_from_group_callbacks():
    api, tg = OnboardingAPI(), Telegram()
    BotService(api, tg).handle_update(
        callback("n:apply:operator:3", chat_type="supergroup")
    )
    assert not api.calls


def test_legacy_management_keyboard_still_opens_admin_menu():
    class AdminAPI(AccessAPI):
        def call(self, method, path, payload=None):
            if path.startswith("/manage?"):
                self.calls.append((method, path, payload))
                return {"parks": [], "jobs": [], "deliveries": [], "health": {}}
            return super().call(method, path, payload)

    api, tg = AdminAPI(), Telegram()
    BotService(api, tg).handle_update(message("⚙️ Управление"))
    assert any(path.startswith("/manage?") for _, path, _ in api.calls)


def test_pending_user_can_request_park_without_task_access():
    api, tg = AccessAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(message("/start"))
    assert (
        tg.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        == "n:request:3"
    )
    service.handle_update(callback("n:request:3"))
    assert (
        "POST",
        "/access/requests",
        {"telegram_user_id": 7, "park_id": 3},
    ) in api.calls
    assert not any("/context" in path or "/robots" in path for _, path, _ in api.calls)


def test_admin_lists_and_resolves_shared_canonical_requests():
    api, tg = AccessAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(message("/requests"))
    assert "Новый механик" in tg.calls[-1][1]["text"]
    service.handle_update(callback("n:approve:10:1"))
    assert (
        "POST",
        "/access/requests/10/decision?telegram_user_id=7",
        {"approve": True, "revision": 1},
    ) in api.calls


def test_mechanic_cannot_use_forged_approval_button_or_group_callback():
    api, tg = API(), Telegram()
    service = BotService(api, tg)
    service.handle_update(callback("n:approve:10:1"))
    service.handle_update(callback("n:request:3", chat_type="supergroup"))
    assert not any(method == "POST" for method, _, _ in api.calls)


@pytest.mark.parametrize(
    "role,expected",
    [
        (
            "royal",
            [
                ["ℹ️ Помощь", "🔍 Робот"],
                ["⚙️ Управление", "📊 Статус"],
                ["⏸ Пауза", "▶️ Старт"],
            ],
        ),
        (
            "admin",
            [
                ["ℹ️ Помощь", "🔍 Робот"],
                ["⚙️ Управление", "📊 Статус"],
                ["⏸ Пауза", "▶️ Старт"],
            ],
        ),
        ("operator", [["ℹ️ Помощь", "🔍 Робот"]]),
        ("mechanic", None),
    ],
)
def test_help_restores_original_role_keyboard(role, expected):
    api, tg = UsageAPI(role), Telegram()
    BotService(api, tg).handle_update(message("ℹ️ Помощь"))
    markup = tg.calls[-1][1]["reply_markup"]
    if expected is None:
        assert markup == {"remove_keyboard": True}
    else:
        assert [[b["text"] for b in row] for row in markup["keyboard"]] == expected
    if role in {"mechanic", "operator"}:
        assert "/admin" not in tg.calls[-1][1]["text"]


@pytest.mark.parametrize(
    "old,view",
    [
        ("tasks", "open"),
        ("hist", "history"),
        ("move", "moves"),
        ("mhist", "moves_history"),
        ("zip", "parts"),
    ],
)
def test_old_robot_buttons_use_fresh_native_permissions(old, view):
    api, tg = API(), Telegram()
    BotService(api, tg).handle_update(callback(f"{old}:a1460"))
    assert any(
        p == f"/robots/a1460?telegram_user_id=7&view={view}" for _, p, _ in api.calls
    )
    assert tg.calls[-1][1]["parse_mode"] == "HTML"


def test_robot_details_have_original_back_and_action_labels():
    api, tg = API(), Telegram()
    BotService(api, tg).handle_update(message("/history 1460"))
    rows = tg.calls[-1][1]["reply_markup"]["inline_keyboard"]
    assert rows[0] == [{"text": "← К задачам", "callback_data": "n:open:a1460"}]
    assert all(len(row) == 1 for row in rows)
    assert "📜 История ремонтов" not in [row[0]["text"] for row in rows]


def test_approval_waits_for_greeting_choice_and_is_bound_to_admin():
    api, tg = AccessAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(callback("n:select:10:1:3"))
    keyboard = tg.calls[-1][1]["reply_markup"]["inline_keyboard"]
    accept, custom = keyboard[0]
    assert accept["text"] == "📝 Приветствие по умолчанию"
    assert not any(method == "POST" for method, _, _ in api.calls)
    service.handle_update(callback(accept["callback_data"], user_id=8))
    assert not any(method == "POST" for method, _, _ in api.calls)
    service.handle_update(callback(custom["callback_data"]))
    service.handle_update(message("Добро пожаловать!"))
    decisions = [payload for method, path, payload in api.calls if "/decision?" in path]
    assert decisions == [{"approve": True, "revision": 1, "target_park_id": 3}]
    service.handle_update(callback(accept["callback_data"]))
    assert len([1 for _, path, _ in api.calls if "/decision?" in path]) == 1


def test_custom_greeting_cancel_and_expiry_do_not_approve(monkeypatch):
    api, tg = AccessAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(callback("n:select:10:1:3"))
    nonce = service.approvals[7]["nonce"]
    service.handle_update(callback(f"n:custom:{nonce}"))
    service.handle_update(message("/cancel"))
    service.handle_update(message("Привет"))
    assert not any(method == "POST" for method, _, _ in api.calls)
    service.handle_update(callback("n:select:10:1:3"))
    flow = service.approvals[7]
    flow["expires"] = -1
    service.handle_update(callback("n:greet:" + flow["nonce"]))
    assert not any(method == "POST" for method, _, _ in api.calls)


def test_access_card_escapes_identity_and_uses_three_park_columns():
    class CardAPI(AccessAPI):
        def call(self, method, path, payload=None):
            if path.startswith("/access/requests?"):
                result = super().call(method, path, payload)
                result[0].update(
                    display_name="<Denis>",
                    telegram_username="denis_1",
                    telegram_user_id=42,
                    user_access_status="pending",
                )
                return result
            if path.startswith("/manage?") or path == "/onboarding/parks":
                return {
                    "parks": [{"id": i, "name": f"Парк {i}"} for i in range(1, 13)],
                    "jobs": [],
                }
            return super().call(method, path, payload)

    tg = Telegram()
    BotService(CardAPI(), tg).handle_update(message("/requests"))
    body = tg.calls[-1][1]
    assert "&lt;Denis&gt;" in body["text"] and "<Denis>" not in body["text"]
    rows = body["reply_markup"]["inline_keyboard"]
    assert [len(row) for row in rows] == [3, 3, 3, 3, 1, 1]
    assert rows[-2][0]["text"] == "🌐 Global"
    assert rows[-1][0]["text"] == "❌ Отклонить"


def test_onboarding_notification_is_scoped_to_api_recipient_and_failure_isolated():
    class NotifyAPI(AccessAPI):
        def call(self, method, path, payload=None):
            if path == "/onboarding/request":
                self.calls.append((method, path, payload))
                return {"state": "pending", "request_id": 10, "notify_admin_ids": [8]}
            return super().call(method, path, payload)

    api, tg = NotifyAPI(), Telegram()
    BotService(api, tg).handle_update(callback("n:apply:mechanic:3"))
    assert any(
        path == "/access/requests?telegram_user_id=8" for _, path, _ in api.calls
    )
    assert [p["chat_id"] for m, p, _ in tg.calls if m == "sendMessage"] == [7, 8]


def test_second_admin_gets_clear_already_decided_response():
    class ResolvedAPI(AccessAPI):
        def call(self, method, path, payload=None):
            if "/decision?" in path:
                raise ServiceError("request_already_resolved", status=409)
            return super().call(method, path, payload)

    tg = Telegram()
    BotService(ResolvedAPI(), tg).handle_update(callback("n:reject:10:1"))
    assert "другой администратор" in tg.calls[-1][1]["text"]


def test_member_removal_requires_actor_bound_confirmation_and_snapshot():
    class UsersAPI(AccessAPI):
        def call(self, method, path, payload=None):
            if path.startswith("/manage/users?"):
                return [
                    {
                        "user_id": 22,
                        "username": "Mechanic",
                        "role": "mechanic",
                        "parks": [{"id": 3, "name": "Северный"}],
                    }
                ]
            if path.startswith("/manage/users/22/parks/3?"):
                self.calls.append((method, path, payload))
                return {}
            return super().call(method, path, payload)

    api, tg = UsersAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(message("/users"))
    assert "Сотрудники ваших парков: 1" in tg.calls[-1][1]["text"]
    service.handle_update(callback("n:revoke:22:3"))
    confirm = tg.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    service.handle_update(callback(confirm, user_id=8))
    assert not any(m == "DELETE" for m, _, _ in api.calls)
    service.handle_update(callback(confirm))
    service.handle_update(callback(confirm))
    assert [(p, v) for m, p, v in api.calls if m == "DELETE"] == [
        ("/manage/users/22/parks/3?telegram_user_id=7", {"expected_park_ids": [3]})
    ]


def test_send_now_requires_confirmation_and_reuses_id_on_api_retry():
    job_id = "17fe4e2a-93e9-4a62-8d6a-806b9fcb0914"

    class ManualAPI(AccessAPI):
        fail = True

        def call(self, method, path, payload=None):
            if path.startswith("/manage?"):
                return {
                    "jobs": [
                        {
                            "id": job_id,
                            "revision": 3,
                            "title": "Объявление",
                            "enabled": False,
                        }
                    ]
                }
            if "/run?" in path:
                self.calls.append((method, path, payload))
                if self.fail:
                    self.fail = False
                    raise ServiceError("api_transport_error", uncertain=True)
                return {"created": False, "delivery": {}}
            return super().call(method, path, payload)

    api, tg = ManualAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(callback(f"n:send:{job_id}:3"))
    confirm = tg.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert len(confirm.encode()) <= 64
    assert not any(method == "POST" for method, _, _ in api.calls)
    service.handle_update(callback(confirm))
    service.handle_update(callback(confirm))
    service.handle_update(callback(confirm))
    sends = [payload for method, path, payload in api.calls if "/run?" in path]
    assert len(sends) == 2
    assert sends[0] == sends[1]
    assert sends[0]["allow_disabled"] is True


JOB_ID = "17fe4e2a-93e9-4a62-8d6a-806b9fcb0914"


class JobAPI(API):
    def __init__(self, job=None):
        super().__init__()
        self.job = {
            "id": JOB_ID,
            "revision": 3,
            "park_id": 3,
            "kind": "zoom",
            "title": "Утренний Zoom",
            "enabled": False,
            "schedule": "daily",
            "timezone": None,
            "time": "09:00",
            "run_at": None,
            "weekdays": [0, 2, 4],
            "start_hour": None,
            "end_hour": None,
            "text": "Подключайтесь",
            "url": "https://zoom.example/room",
            "tracker_tag": None,
            "alternate": "even",
            "anchor_date": "2026-10-05",
            **(job or {}),
        }
        self.reject_next_update = False

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.startswith("/context?"):
            return {"role": "admin", "can_manage": True}
        if path.startswith("/manage?"):
            return {
                "parks": [
                    {
                        "id": 3,
                        "name": "Северный",
                        "tag": "north",
                        "chat_id": -100,
                        "thread_id": None,
                        "revision": 1,
                    }
                ],
                "jobs": [self.job],
            }
        if method == "PUT" and path.startswith(f"/manage/jobs/{JOB_ID}?"):
            if self.reject_next_update:
                self.reject_next_update = False
                raise ServiceError("invalid_job", status=422)
            if payload["revision"] != self.job["revision"]:
                raise ServiceError("revision_conflict", status=409)
            self.job = {**payload, "revision": payload["revision"] + 1}
            return self.job
        if method == "POST" and path.startswith("/manage/jobs?"):
            self.job = {"id": JOB_ID, "revision": 1, **payload}
            return self.job
        return super().call(method, path, payload)


def _button(payload, label):
    return next(
        button
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
        if button["text"] == label
    )


def test_job_card_uses_friendly_schedule_timezone_and_alternation_summary():
    tg = Telegram()
    BotService(JobAPI(), tg).handle_update(callback(f"n:job:{JOB_ID}"))

    payload = tg.calls[-1][1]
    assert "Ежедневно в 09:00" in payload["text"]
    assert "Пн, Ср, Пт" in payload["text"]
    assert "Часовой пояс: как у парка" in payload["text"]
    assert "Группа A · опорная дата 05.10.2026" in payload["text"]
    assert len(_button(payload, "Настройки")["callback_data"].encode()) <= 64


def test_job_settings_offer_all_guided_fields_with_bounded_callbacks():
    api, tg = JobAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(callback(f"n:cfg:{JOB_ID}:3"))

    payload = tg.calls[-1][1]
    labels = {
        button["text"]
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
    }
    assert {
        "Название",
        "Ссылка",
        "Дни недели",
        "Часовой пояс",
        "A/B и дата",
        "Расписание",
    } <= labels
    assert all(
        len(button["callback_data"].encode()) <= 64
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
    )

    service.handle_update(callback(_button(payload, "A/B и дата")["callback_data"]))
    assert "even 2026-10-05" in tg.calls[-1][1]["text"]


def test_report_settings_edit_tracker_tag_through_guided_input():
    api, tg = JobAPI({"kind": "report", "tracker_tag": "old"}), Telegram()
    service = BotService(api, tg)
    service.handle_update(callback(f"n:cfg:{JOB_ID}:3"))

    payload = tg.calls[-1][1]
    tag_button = _button(payload, "Tracker-тег")
    assert len(tag_button["callback_data"].encode()) <= 64
    service.handle_update(callback(tag_button["callback_data"]))
    service.handle_update(message("zoom"))

    update = next(payload for method, _, payload in api.calls if method == "PUT")
    assert update["tracker_tag"] == "zoom"


def test_guided_input_accepts_russian_weekdays_and_ab_aliases():
    api, tg = (
        JobAPI({"weekdays": list(range(7)), "alternate": "all", "anchor_date": None}),
        Telegram(),
    )
    service = BotService(api, tg)
    service.handle_update(callback(f"n:days:{JOB_ID}:3"))
    service.handle_update(message("Пн, Ср, Пт"))
    assert api.job["weekdays"] == [0, 2, 4]

    service.handle_update(callback(f"n:alt:{JOB_ID}:4"))
    service.handle_update(message("B 2026-10-05"))
    assert api.job["alternate"] == "odd"
    assert api.job["anchor_date"] == "2026-10-05"


def test_hourly_time_edit_retries_invalid_and_422_then_saves_both_hours_atomically():
    api = JobAPI(
        {
            "schedule": "hourly",
            "time": None,
            "weekdays": list(range(7)),
            "start_hour": 9,
            "end_hour": 18,
            "alternate": "all",
            "anchor_date": None,
        }
    )
    tg = Telegram()
    service = BotService(api, tg)
    service.handle_update(callback(f"n:time:{JOB_ID}:3"))
    service.handle_update(message("22-24"))
    assert 7 in service.pending
    assert not any(method == "PUT" for method, _, _ in api.calls)

    api.reject_next_update = True
    service.handle_update(message("0-23"))
    assert 7 in service.pending
    service.handle_update(message("0-23"))

    updates = [payload for method, path, payload in api.calls if method == "PUT"]
    assert len(updates) == 2
    assert updates[-1]["start_hour"] == 0
    assert updates[-1]["end_hour"] == 23
    assert updates[-1]["revision"] == 3
    assert 7 not in service.pending


def test_once_time_edit_updates_aware_run_at_without_writing_daily_time():
    api = JobAPI(
        {
            "schedule": "once",
            "timezone": "Europe/Moscow",
            "time": None,
            "run_at": "2026-12-01T09:00:00+03:00",
            "weekdays": [],
            "start_hour": None,
            "end_hour": None,
            "alternate": "all",
            "anchor_date": None,
        }
    )
    service = BotService(api, Telegram())
    service.handle_update(callback(f"n:time:{JOB_ID}:3"))
    service.handle_update(message("2026-12-02T10:30:00+03:00"))

    update = next(payload for method, _, payload in api.calls if method == "PUT")
    assert update["run_at"] == "2026-12-02T10:30:00+03:00"
    assert update["time"] is None
    assert update["schedule"] == "once"


def test_pending_job_edit_rejects_changed_revision_snapshot():
    api, tg = JobAPI(), Telegram()
    service = BotService(api, tg)
    service.handle_update(callback(f"n:title:{JOB_ID}:3"))
    api.job["revision"] = 4
    service.handle_update(message("Новое название"))

    assert not any(method == "PUT" for method, _, _ in api.calls)
    assert 7 not in service.pending
    assert "изменились" in tg.calls[-1][1]["text"].lower()


@pytest.mark.parametrize(
    "navigation", [message("🔍 Робот"), message("/help"), callback(f"n:job:{JOB_ID}")]
)
def test_leaving_editor_does_not_save_a_robot_number_as_a_job_title(navigation):
    api = JobAPI()
    service = BotService(api, Telegram())
    service.handle_update(callback(f"n:title:{JOB_ID}:3"))
    service.handle_update(navigation)
    service.handle_update(message("1460"))

    assert 7 not in service.pending
    assert not any(method == "PUT" for method, _, _ in api.calls)
    assert any(path.startswith("/robots/a1460?") for _, path, _ in api.calls)


def test_park_jobs_are_paginated_twenty_per_page():
    class ManyJobsAPI(JobAPI):
        def call(self, method, path, payload=None):
            if path.startswith("/manage?"):
                state = super().call(method, path, payload)
                state["jobs"] = [
                    {
                        **self.job,
                        "id": f"00000000-0000-0000-0000-{index:012d}",
                        "title": f"Job {index}",
                    }
                    for index in range(45)
                ]
                return state
            return super().call(method, path, payload)

    tg = Telegram()
    service = BotService(ManyJobsAPI(), tg)
    service.handle_update(callback("n:park:3"))
    first = tg.calls[-1][1]
    assert (
        len(
            [
                row
                for row in first["reply_markup"]["inline_keyboard"]
                if row[0]["text"].startswith("○")
            ]
        )
        == 20
    )
    service.handle_update(callback(_button(first, "Далее →")["callback_data"]))
    second = tg.calls[-1][1]
    assert "Job 20" in str(second)
    assert "Job 40" not in str(second)


def test_new_zoom_defaults_to_explicit_moscow_timezone():
    api, tg = JobAPI(), Telegram()
    BotService(api, tg).handle_update(callback("n:newjob:3:zoom"))
    created = next(payload for method, path, payload in api.calls if method == "POST")
    assert created["timezone"] == "Europe/Moscow"


@pytest.mark.parametrize("view", ["moves", "moves_history", "parts"])
def test_related_robot_views_keep_authenticated_scope(view):
    api, tg = API(), Telegram()
    BotService(api, tg).handle_update(message(f"/{view} 1460"))
    assert any(
        p == f"/robots/a1460?telegram_user_id=7&view={view}" for _, p, _ in api.calls
    )


def test_health_expires_stalled_polling_and_scheduler_independently():
    state = {
        "telegram_ok": True,
        "scheduler_ok": True,
        "last_error": None,
        "poll_at": 100,
        "scheduler_at": 1,
    }
    assert health_snapshot(state, now=189)["telegram_ok"] is True
    assert health_snapshot(state, now=190)["telegram_ok"] is False
    assert health_snapshot(state, now=300)["scheduler_ok"] is True
    assert health_snapshot(state, now=301)["scheduler_ok"] is False


def test_slow_user_does_not_block_another_and_each_user_keeps_command_order():
    from threading import Event
    from types import SimpleNamespace

    first_started, release, fast_completed = Event(), Event(), Event()
    completed = []

    class Service:
        api = telegram = SimpleNamespace(close=lambda: None)

        def handle_update(self, update):
            value = update["message"]["text"]
            if value == "slow":
                first_started.set()
                assert release.wait(timeout=5)
            if value == "fast":
                fast_completed.set()
            completed.append(value)

    workers = UpdateWorkers(Service)
    try:
        slow = workers.submit(message("slow", user_id=1))
        assert first_started.wait(timeout=2)
        later = workers.submit(message("same-user-next", user_id=1))
        fast = workers.submit(message("fast", user_id=2))
        assert fast_completed.wait(timeout=2)
        assert not later.done()
        release.set()
        for future in (slow, later, fast):
            future.result(timeout=2)
        assert completed.index("slow") < completed.index("same-user-next")
    finally:
        release.set()
        workers.close()


def test_disabled_job_between_claim_and_begin_is_not_sent():
    api, tg = API(), Telegram()
    api.fail_begin = True
    BotService(api, tg).deliver(delivery())
    assert not tg.calls


def test_ambiguous_send_is_recorded_unknown_without_retry():
    api, tg = API(), Telegram()
    tg.fail_send = True
    BotService(api, tg).deliver(delivery())
    sends = [call for call in tg.calls if call[0] == "sendMessage"]
    assert len(sends) == 1
    assert api.calls[-1][2]["state"] == "unknown"
    assert "test-lease" not in api.calls[-1][2]["error_code"]


def test_zoom_uses_thread_and_only_replaces_link_placeholder():
    api, tg = API(), Telegram()
    BotService(api, tg).deliver(delivery())
    payload = tg.calls[0][1]
    assert payload["message_thread_id"] == 12
    assert payload["chat_id"] == -1001234567890
    assert "{обычный текст}" in payload["text"]
    assert "https://zoom.example/meeting" in payload["text"]
    assert api.calls[-1][2]["state"] == "sent"


def test_campaign_preserves_instruction_and_link_in_image_caption(monkeypatch):
    class ContentAPI(API):
        def call(self, method, path, payload=None):
            if path.endswith("/content"):
                return {"issues": [{"key": "SDCFLEETOPS-1"}], "truncated": False}
            return super().call(method, path, payload)

    monkeypatch.setattr(
        "native.service.render_campaign", lambda *args, **kwargs: b"photo"
    )
    api, tg = ContentAPI(), Telegram()
    item = delivery()
    item["job"].update(kind="campaign", text="Инструкция: {link}")
    BotService(api, tg).deliver(item)
    method, payload, _ = tg.calls[0]
    assert method == "sendPhoto"
    assert "Инструкция: https://zoom.example/meeting" in payload["caption"]
    assert api.calls[-1][2]["state"] == "sent"


def test_issue_response_is_bounded_and_marks_truncation():
    issues = [
        {
            "key": f"SDCFLEETOPS-{n}",
            "summary": "X" * 500,
            "status": {"key": "inProgress"},
            "description": "**Comment:** Проверить привод",
        }
        for n in range(80)
    ]
    text = format_issues("a1460", issues, history=False, truncated=True)
    assert len(text) <= 3900
    assert "Проверить привод" in text
    assert "Робот <b>a1460</b>, открытые задачи: 80" in text
    assert "обрезан" in text.lower()


def test_update_offset_survives_restart_and_imports_previous_runtime(tmp_path):
    (tmp_path / "dispatcher_bot.offset").write_text("1441")
    assert read_offset(tmp_path) == 1441
    save_offset(tmp_path, 1442)
    assert read_offset(tmp_path) == 1442
    assert (tmp_path / "dispatcher_bot.offset").read_text() == "1441"
    assert (tmp_path / "native-update-offset.json").stat().st_mode & 0o777 == 0o600


def test_second_polling_process_is_rejected(tmp_path):
    import os

    descriptor = acquire_instance(tmp_path)
    try:
        with pytest.raises(BlockingIOError):
            acquire_instance(tmp_path)
    finally:
        os.close(descriptor)


def test_native_runtime_processes_batch_persists_offset_and_closes_without_network(
    tmp_path, monkeypatch
):
    import os
    from threading import Lock

    from native import runtime

    sent, clients = [], []
    lock = Lock()

    class RuntimeAPI(API):
        def __init__(self, *_args, **_kwargs):
            super().__init__()
            self.closed = False
            clients.append(self)

        def call(self, method, path, payload=None):
            if path == "/claim":
                return {"deliveries": []}
            return super().call(method, path, payload)

        def close(self):
            self.closed = True

    class RuntimeTelegram:
        def __init__(self, *_):
            self.polled = False
            self.closed = False
            clients.append(self)

        def call(self, method, payload=None):
            if method == "getUpdates":
                if self.polled:
                    raise KeyboardInterrupt
                self.polled = True
                return [
                    {**message("1460", user_id=n), "update_id": 100 + n}
                    for n in range(1, 5)
                ]
            if method == "sendMessage":
                with lock:
                    sent.append(payload)
            return {}

        def close(self):
            self.closed = True

    monkeypatch.setattr(runtime, "APIClient", RuntimeAPI)
    monkeypatch.setattr(runtime, "TelegramClient", RuntimeTelegram)
    heartbeat = os.open(tmp_path / "heartbeat", os.O_CREAT | os.O_RDWR, 0o600)
    ready = tmp_path / "ready"
    try:
        with pytest.raises(KeyboardInterrupt):
            runtime.run_service(
                data_dir=tmp_path,
                api_url="http://api",
                bridge_key="test",
                token="test",
                ready_file=ready,
                heartbeat_fd=heartbeat,
            )
        assert len(sent) == 4
        assert read_offset(tmp_path) == 105
        assert not ready.exists()
        assert all(client.closed for client in clients)
        descriptor = acquire_instance(tmp_path)
        os.close(descriptor)
    finally:
        os.close(heartbeat)


def test_report_image_uses_live_park_label_and_is_bounded():
    import io

    from PIL import Image

    result = render_report(
        {"kind": "report", "title": "Открытые блокеры"},
        {"name": "Новый парк"},
        [
            {
                "key": f"SDCFLEETOPS-{n}",
                "summary": "Проверить двигатель робота",
                "status": {"key": "inProgress"},
            }
            for n in range(100)
        ],
        truncated=True,
    )
    with Image.open(io.BytesIO(result)) as picture:
        assert picture.format == "PNG"
        assert picture.width + picture.height < 10000
    assert len(result) < 9 * 1024 * 1024


def test_startup_failure_publishes_safe_health_and_never_marks_ready(
    tmp_path, monkeypatch
):
    import os

    from native import runtime

    reports = []

    class HealthAPI:
        def __init__(self, *_args, **_kwargs):
            pass

        def call(self, method, path, payload=None):
            reports.append((path, payload))

        def close(self):
            pass

    class RejectedTelegram(HealthAPI):
        def call(self, *_args, **_kwargs):
            raise ServiceError("telegram_http_401", status=401)

    monkeypatch.setattr(runtime, "APIClient", HealthAPI)
    monkeypatch.setattr(runtime, "TelegramClient", RejectedTelegram)
    heartbeat = os.open(tmp_path / "heartbeat", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with pytest.raises(ServiceError, match="telegram_http_401"):
            runtime.run_service(
                data_dir=tmp_path,
                api_url="http://api",
                bridge_key="test",
                token="test",
                ready_file=tmp_path / "ready",
                heartbeat_fd=heartbeat,
            )
        assert reports == [
            (
                "/health",
                {
                    "telegram_ok": False,
                    "scheduler_ok": False,
                    "last_error": "telegram_http_401",
                },
            )
        ]
        assert not (tmp_path / "ready").exists()
        descriptor = acquire_instance(tmp_path)
        os.close(descriptor)
    finally:
        os.close(heartbeat)


class ReportAPI(API):
    def __init__(self, count=41):
        super().__init__()
        self.issues = [
            {
                "key": f"SDCFLEETOPS-{n + 1}",
                "summary": f"[a{n + 1}] Ремонт",
                "status": {"key": "inProgress"},
                "bot_report": {"repair_hours": 2, "downtime_hours": 48},
            }
            for n in range(count)
        ]

    def call(self, method, path, payload=None):
        if path.endswith("/content"):
            self.calls.append((method, path, payload))
            return {"issues": self.issues, "truncated": False, "report_summary": {}}
        return super().call(method, path, payload)


def test_large_report_sends_all_pages_and_original_watchdog_in_same_topic():
    api, tg = ReportAPI(), Telegram()
    item = delivery()
    item["job"].update(kind="report", text=None, url=None)
    BotService(api, tg).deliver(item)
    assert [call[0] for call in tg.calls] == ["sendPhoto", "sendPhoto", "sendMessage"]
    assert all(call[1]["message_thread_id"] == 12 for call in tg.calls)
    assert (
        tg.calls[-1][1]["text"]
        == "Задачи с превышением времени в очереди — отсутствуют"
    )
    assert tg.calls[-1][1]["parse_mode"] == "HTML"
    assert api.calls[-1][2]["state"] == "sent"


def test_partial_report_failure_records_progress_without_resending():
    class FailingSecondPhoto(Telegram):
        def call(self, method, payload=None, **kwargs):
            result = super().call(method, payload, **kwargs)
            if len(self.calls) == 2:
                raise ServiceError("telegram_transport_error", uncertain=True)
            return result

    api, tg = ReportAPI(), FailingSecondPhoto()
    item = delivery()
    item["job"].update(kind="report", text=None, url=None)
    BotService(api, tg).deliver(item)
    assert len(tg.calls) == 2
    assert api.calls[-1][2]["state"] == "unknown"
    assert api.calls[-1][2]["error_code"].startswith("partial_1_")


def test_campaign_without_issues_still_sends_campaign_chart():
    api, tg = ReportAPI(count=0), Telegram()
    item = delivery()
    item["job"].update(kind="campaign", tracker_tag="SK_TEST", text=None, url=None)
    BotService(api, tg).deliver(item)
    assert [call[0] for call in tg.calls] == ["sendPhoto"]
    from io import BytesIO

    from PIL import Image

    assert Image.open(BytesIO(tg.calls[0][2]["photo"])).size == (1080, 1080)


class UsageAPI(API):
    def __init__(self, role="royal"):
        super().__init__()
        self.role = role

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.startswith("/context?"):
            return {"role": self.role, "can_manage": self.role in {"admin", "royal"}}
        if path.startswith("/usage?"):
            return {
                "user_id": 1,
                "username": "Тест",
                "telegram_user_id": 7,
                "stats": {"today": 3, "month": 12, "total": 42},
            }
        if path.startswith("/control?"):
            return payload or {
                "queries_paused": False,
                "deliveries_paused": True,
                "revision": 4,
            }
        if path.startswith("/robots/"):
            return {
                "issues": [],
                "truncated": False,
                "greeting": "Доброе утро, Механик!",
                "usage": {"today": 1, "month": 10, "total": 40},
            }
        return super().call(method, path, payload)


def test_robot_daily_greeting_is_shown_without_extra_round_trip():
    api, tg = UsageAPI(), Telegram()
    BotService(api, tg).handle_update(message("1460"))
    assert "Доброе утро, Механик!" in tg.calls[0][1]["text"]
    assert sum("/robots/" in path for _, path, _ in api.calls) == 1


def test_stats_shows_persistent_today_month_and_total():
    api, tg = UsageAPI("mechanic"), Telegram()
    BotService(api, tg).handle_update(message("/stats"))
    assert "3" in tg.calls[-1][1]["text"]
    assert "12" in tg.calls[-1][1]["text"]
    assert "42" in tg.calls[-1][1]["text"]
    assert any(path == "/usage?telegram_user_id=7" for _, path, _ in api.calls)


def test_royal_can_pause_queries_without_changing_delivery_pause():
    api, tg = UsageAPI(), Telegram()
    BotService(api, tg).handle_update(message("/pause queries"))
    assert (
        "PUT",
        "/control?telegram_user_id=7",
        {
            "queries_paused": True,
            "deliveries_paused": True,
            "revision": 4,
        },
    ) in api.calls


def test_admin_cannot_pause_service_globally():
    api, tg = UsageAPI("admin"), Telegram()
    BotService(api, tg).handle_update(message("/pause all"))
    assert not any(method == "PUT" for method, _, _ in api.calls)
    assert "royal" in tg.calls[-1][1]["text"].lower()


def test_qr_command_sends_yasadr_image_only_after_server_permission_check():
    api, tg = UsageAPI(), Telegram()
    original = api.call

    def with_anchor(method, path, payload=None):
        result = original(method, path, payload)
        if path.startswith("/robots/"):
            result["issues"] = [{"key": "SDCFLEETOPS-1"}]
        return result

    api.call = with_anchor
    BotService(api, tg).handle_update(message("/qr 1460"))
    assert any(
        path == "/robots/a1460?telegram_user_id=7&view=open" for _, path, _ in api.calls
    )
    assert tg.calls[-1][0] == "sendPhoto"
    assert tg.calls[-1][1]["caption"] == "YASADR00000001460"
    assert tg.calls[-1][2]["photo"].startswith(b"\x89PNG")


def test_paused_report_does_not_send_or_mark_failed_receipt():
    class PausedAPI(API):
        def call(self, method, path, payload=None):
            if path.endswith("/begin"):
                raise ServiceError("native_deliveries_paused", status=409)
            return super().call(method, path, payload)

    api, tg = PausedAPI(), Telegram()
    BotService(api, tg).deliver(delivery())
    assert not tg.calls
    assert not any(path.endswith("/finish") for _, path, _ in api.calls)


def test_qr_without_scoped_robot_anchor_is_denied():
    api, tg = API(), Telegram()
    BotService(api, tg).handle_update(message("/qr 999999"))
    assert not any(call[0] == "sendPhoto" for call in tg.calls)
    assert "QR-код недоступен" in tg.calls[-1][1]["text"]
