"""System: pauses, tokens, heal."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable

from admin.nav import back_home_row, cached_health_snapshot, invalidate_health_cache
from admin.sessions import clear_session, get_session
from store.roles import is_full_admin
from store.runtime import set_dispatcher_paused, set_send_paused

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]

from paths import LOGS_DIR, ROOT
from store.schedules import send_window_label


def _sender_python() -> Path:
    venv_py = ROOT / "venv" / "bin" / "python"
    if venv_py.is_file():
        return venv_py
    return Path(sys.executable)


def _tail_log(path: Path, n: int = 24) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(нет лога)"
    return "\n".join(lines[-n:])[-3500:]


def _run_png_send_and_notify(user_id: int, send: SendFn) -> None:
    py = _sender_python()
    script = ROOT / "app" / "telegram_sender.py"
    log_path = LOGS_DIR / "telegram_sender_manual.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "app"),
        "TZ": "Europe/Moscow",
        "PYTHONUNBUFFERED": "1",
    }
    rc = -1
    try:
        with open(log_path, "a", encoding="utf-8") as logf:
            logf.write(f"\n--- manual send by {user_id} at {datetime.now().isoformat()} ---\n")
            logf.flush()
            proc = subprocess.run(
                [str(py), str(script), "--force"],
                cwd=str(ROOT),
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=20 * 60,
            )
            rc = proc.returncode
    except subprocess.TimeoutExpired:
        send(
            user_id,
            "⏱ PNG-рассылка не уложилась в 20 минут — смотрите "
            "<code>logs/telegram_sender_manual.log</code>",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        send(user_id, f"Не удалось выполнить рассылку: {e}")
        return
    tail = escape(_tail_log(log_path))
    kb = {"inline_keyboard": [[{"text": "« Статус", "callback_data": "adm:sys:status"}]]}
    if rc == 0:
        send(
            user_id,
            f"✅ PNG-рассылка завершена.\n<pre>{tail}</pre>",
            parse_mode="HTML",
            reply_markup=kb,
        )
        return
    if rc == 2:
        send(
            user_id,
            "⏸️ Уже идёт другая PNG-рассылка. Подождите и нажмите ещё раз.",
            reply_markup=kb,
        )
        return
    send(
        user_id,
        "❌ PNG не ушли в чаты.\n"
        "Проверьте интеграцию Tracker в настройках Robopark.\n"
        f"<pre>{tail}</pre>",
        parse_mode="HTML",
        reply_markup=kb,
    )


def handle_sys_callback(
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
    action = parts[2] if len(parts) > 2 else "status"

    if action == "status":
        answer(callback_id)
        h = cached_health_snapshot()
        disp = "⏸" if h["dispatcher_paused"] else "▶️"
        send_p = "⏸" if h["send_paused"] else "▶️"
        alive = "🟢" if h["pid_alive"] else "🔴"
        text = (
            f"<b>⏯ Паузы и статус</b>\n"
            f"{alive} bot · disp {disp} · send {send_p}\n"
            f"profile: <code>{h['profile']}</code>\n"
            f"MSK: <code>{h.get('msk_now') or '—'}</code> · host TZ: <code>{h.get('host_tz') or '—'}</code>\n"
            f"pid: <code>{h.get('pid') or '—'}</code>\n"
            f"PNG: {send_window_label()}\n"
            f"RSS: {h.get('rss_mb', 0)} МБ · FD {h.get('fd_count', 0)}"
        )
        warns = h.get("warnings") or []
        if warns:
            text += "\n⚠️ " + "; ".join(str(w) for w in warns)
        kb = {
            "inline_keyboard": [
                [{"text": "⏯ Пауза диспетчера", "callback_data": "adm:sys:tog_disp"}],
                [{"text": "⏯ Пауза рассылки", "callback_data": "adm:sys:tog_send"}],
                [{"text": "📤 PNG сейчас", "callback_data": "adm:sys:send_ask"}],
                [{"text": "« Система", "callback_data": "adm:menu:sys"}],
                back_home_row(),
            ]
        }
        send(user_id, text, parse_mode="HTML", reply_markup=kb)
        return True

    if action == "send_ask":
        answer(callback_id)
        h = cached_health_snapshot()
        warn = ""
        if h["send_paused"]:
            warn = "\n⚠️ Сейчас <b>send pause</b> — принудительный запуск обойдёт паузу."
        send(
            user_id,
            f"Запустить полную рассылку (Tracker → PNG → Telegram) сейчас?{warn}\n"
            f"Расписание обычное: {send_window_label()}",
            parse_mode="HTML",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "✅ Запустить", "callback_data": "adm:sys:send_run"}],
                    [{"text": "« Назад", "callback_data": "adm:menu:send"}],
                ]
            },
        )
        return True

    if action == "send_run":
        answer(callback_id, "start…")
        script = ROOT / "app" / "telegram_sender.py"
        if not script.is_file():
            send(user_id, f"Нет файла {script}")
            return True
        send(
            user_id,
            "PNG-рассылка запущена (Tracker → таблички → чаты парков).\n"
            "Это занимает несколько минут. Напишу сюда, когда закончится.",
        )
        threading.Thread(
            target=_run_png_send_and_notify,
            args=(user_id, send),
            daemon=True,
            name="png-send-manual",
        ).start()
        return True

    if action == "tog_disp":
        h = cached_health_snapshot()
        set_dispatcher_paused(not h["dispatcher_paused"])
        invalidate_health_cache()
        answer(callback_id, "ok")
        return handle_sys_callback(
            user_id=user_id,
            parts=["adm", "sys", "status"],
            callback_id=callback_id,
            send=send,
            answer=lambda *a, **k: None,
        )

    if action == "tog_send":
        h = cached_health_snapshot()
        set_send_paused(not h["send_paused"])
        invalidate_health_cache()
        answer(callback_id, "ok")
        return handle_sys_callback(
            user_id=user_id,
            parts=["adm", "sys", "status"],
            callback_id=callback_id,
            send=send,
            answer=lambda *a, **k: None,
        )

    if action == "tokens":
        answer(callback_id)
        text = (
            "<b>Токены</b>\n"
            "Tracker и Telegram настраиваются владельцем в Robopark. "
            "Бот не хранит отдельный Tracker-токен."
        )
        kb = {
            "inline_keyboard": [
                [{"text": "« Хост", "callback_data": "adm:menu:host"}],
                back_home_row(),
            ]
        }
        send(user_id, text, parse_mode="HTML", reply_markup=kb)
        return True

    if action in {"tok_tr", "tok_tg"}:
        answer(callback_id)
        send(
            user_id,
            "Токены настраиваются владельцем в Robopark. Отправлять их в Telegram не нужно.",
        )
        return True

    answer(callback_id)
    return True


def handle_token_text(*, user_id: int, text: str, send: SendFn) -> bool:
    sess = get_session(user_id)
    if not sess:
        return False
    if text.strip() == "/cancel":
        clear_session(user_id)
        send(user_id, "Отменено.")
        return True

    kind = sess.get("kind")
    if kind in {"tracker_token", "tg_token_wait", "tg_token_confirm"}:
        clear_session(user_id)
        send(user_id, "Токены настраиваются владельцем в Robopark. Отправлять их в Telegram не нужно.")
        return True

    return False


def handle_heal_callback(
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
    action = parts[2] if len(parts) > 2 else "ask"
    if action == "ask":
        answer(callback_id)
        send(
            user_id,
            "Heal: sidecar locations, stuck OTA, previous Telegram token (если текущий мёртв / force), restart.\nПодтвердить?",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "CONFIRM heal", "callback_data": "adm:heal:run"}],
                    [{"text": "« Хост", "callback_data": "adm:menu:host"}],
                ]
            },
        )
        return True
    if action == "run":
        answer(callback_id, "heal…")
        script = ROOT / "deploy" / "heal.sh"
        if script.is_file():
            subprocess.Popen(
                ["bash", str(script), "--restore-token"],
                cwd=str(ROOT),
                start_new_session=True,
            )
            send(user_id, "Heal запущен (см. logs / deploy).")
        else:
            send(user_id, "Нет deploy/heal.sh")
        return True
    answer(callback_id)
    return True
