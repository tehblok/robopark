"""ZIP OTA via Telegram (validate → confirm → host script)."""

from __future__ import annotations

import json
import subprocess
import time
import zipfile
from html import escape
from pathlib import Path
from typing import Any, Callable

from admin.sessions import clear_session, get_session, set_session
from store.roles import admin_user_ids, is_full_admin

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]

from paths import ROOT

OTA_DIR = ROOT / "data" / "ota"
PENDING_NOTIFY = OTA_DIR / "pending_notify.json"
LAST_RESULT = OTA_DIR / "last_result.json"
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
PACKAGE_MARKER = "dispatcher_bot.py"
OTA_WAIT_TTL = 600.0
FORBIDDEN_BASENAMES = frozenset({"secrets.env", "credentials.py"})


def validate_ota_zip(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "file missing"
    size = path.stat().st_size
    if size > MAX_ZIP_BYTES:
        return False, f"too large ({size} > {MAX_ZIP_BYTES})"
    if size < 100:
        return False, "too small"
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            uncompressed = 0
            for info in zf.infolist():
                name = info.filename
                if name.startswith("/") or ".." in Path(name).parts:
                    return False, f"zip-slip: {name}"
                base = Path(name).name
                if base in FORBIDDEN_BASENAMES:
                    return False, f"secret file forbidden: {name}"
                if not name.endswith("/"):
                    uncompressed += max(0, int(info.file_size or 0))
                    if uncompressed > MAX_UNCOMPRESSED_BYTES:
                        return False, "zip bomb: uncompressed too large"
            if not any(
                Path(n).name == PACKAGE_MARKER for n in names if not n.endswith("/")
            ):
                return False, f"missing marker {PACKAGE_MARKER}"
    except zipfile.BadZipFile:
        return False, "bad zip"
    return True, "ok"


def persist_ota_wait(user_id: int) -> None:
    set_session(user_id, "ota_wait_doc")
    OTA_DIR.mkdir(parents=True, exist_ok=True)
    (OTA_DIR / "wait_session.json").write_text(
        json.dumps({"user_id": int(user_id), "kind": "ota_wait_doc", "ts": time.time()})
        + "\n",
        encoding="utf-8",
    )


def is_waiting_ota_doc(user_id: int) -> bool:
    sess = get_session(user_id)
    if sess and sess.get("kind") == "ota_wait_doc":
        return True
    path = OTA_DIR / "wait_session.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if int(data.get("user_id") or 0) != int(user_id):
        return False
    ts = float(data.get("ts") or 0)
    if time.time() - ts > OTA_WAIT_TTL:
        try:
            path.unlink()
        except OSError:
            pass
        return False
    return data.get("kind") == "ota_wait_doc"


def ota_zip_file(zip_name: str) -> Path | None:
    """Basename inside data/ota only. Rejects zip-slip names from callback_data."""
    name = str(zip_name or "")
    if not name or name != Path(name).name or name.startswith("."):
        return None
    root = OTA_DIR.resolve()
    path = (OTA_DIR / name).resolve()
    if path.parent != root:
        return None
    return path


def launch_ota_apply(zip_path: Path) -> tuple[bool, str]:
    """Start ota_update.sh outside the dispatcher cgroup. True if a launcher ran.

    ensure_systemd_units.sh is not a success path: it exits 0 after installing
    units and would otherwise report OTA as started without applying the ZIP.
    """
    zip_path = zip_path.resolve()
    if zip_path.parent != OTA_DIR.resolve():
        return False, "zip outside data/ota"
    OTA_DIR.mkdir(parents=True, exist_ok=True)
    (OTA_DIR / "apply.requested").write_text(str(zip_path) + "\n", encoding="utf-8")
    spawn = ROOT / "deploy" / "spawn_ota.sh"
    attempts: list[list[str]] = []
    if spawn.is_file():
        attempts.append(["sudo", "-n", "bash", str(spawn), str(zip_path)])
        attempts.append(["bash", str(spawn), str(zip_path)])
    last = "no launcher"
    for cmd in attempts:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=90,
            )
        except Exception as e:
            last = str(e)
            continue
        if proc.returncode == 0:
            return True, " ".join(cmd[:4])
        last = (proc.stderr or proc.stdout or f"exit {proc.returncode}")[:300]
    ota_sh = ROOT / "deploy" / "ota_update.sh"
    if ota_sh.is_file():
        try:
            subprocess.Popen(
                ["bash", str(ota_sh), str(zip_path)],
                cwd=str(ROOT),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True, "detached"
        except Exception as e:
            last = str(e)
    return False, last


def write_pending_notify(*, user_id: int, zip_name: str) -> None:
    OTA_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_NOTIFY.write_text(
        json.dumps(
            {"user_id": int(user_id), "zip": zip_name, "ts": int(time.time())},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def format_ota_result_message(data: dict[str, Any]) -> str:
    zip_name = escape(str(data.get("zip") or "?"))
    if data.get("ok"):
        return (
            f"✅ <b>OTA успешна</b>\n"
            f"Архив: <code>{zip_name}</code>\n"
            f"Бот перезапущен и снова в работе."
        )
    reason = escape(str(data.get("reason") or "unknown"))
    return (
        f"❌ <b>OTA не удалась</b>\n"
        f"Архив: <code>{zip_name}</code>\n"
        f"Причина: <code>{reason}</code>\n"
        f"Проверьте <code>data/ota/</code> и логи; при необходимости <code>deploy/heal.sh</code>."
    )


def deliver_ota_result_notification(send: SendFn) -> bool:
    """On bot startup: notify admins about last OTA result once."""
    if not LAST_RESULT.is_file():
        return False
    try:
        data = json.loads(LAST_RESULT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if data.get("notified"):
        return False

    text = format_ota_result_message(data)
    recipients: set[int] = set(admin_user_ids())
    uid = data.get("user_id")
    if uid:
        recipients.add(int(uid))
    delivered = False
    for admin_id in sorted(recipients):
        try:
            result = send(admin_id, text, parse_mode="HTML")
        except Exception:
            continue
        if result is None or result is False:
            continue
        delivered = True
    if not delivered:
        return False

    data["notified"] = True
    try:
        LAST_RESULT.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        return False
    return True


def handle_ota_callback(
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
    action = parts[2] if len(parts) > 2 else "menu"
    if action == "menu":
        answer(callback_id)
        persist_ota_wait(user_id)
        send(
            user_id,
            "Пришлите ZIP-архив релиза <b>в личку</b>.\n"
            f"Лимит {MAX_ZIP_BYTES // (1024 * 1024)} MiB. После проверки будет CONFIRM.\n"
            "После применения бот пришлёт сообщение об успехе или ошибке.",
            parse_mode="HTML",
            reply_markup={"inline_keyboard": [[{"text": "« Хост", "callback_data": "adm:menu:host"}]]},
        )
        return True
    if action == "apply" and len(parts) > 3:
        answer(callback_id, "apply…")
        zip_name = parts[3]
        zip_path = ota_zip_file(zip_name)
        if zip_path is None:
            send(user_id, "Некорректное имя ZIP")
            return True
        ok, reason = validate_ota_zip(zip_path)
        if not ok:
            send(user_id, f"Невалидный ZIP: {reason}")
            return True
        script = ROOT / "deploy" / "ota_update.sh"
        if not script.is_file():
            send(user_id, "Нет deploy/ota_update.sh")
            return True
        clear_session(user_id)
        write_pending_notify(user_id=user_id, zip_name=zip_name)
        ok_launch, how = launch_ota_apply(zip_path)
        if not ok_launch:
            send(
                user_id,
                "OTA не запустилась из-под бота (systemd убивает дочерний процесс).\n"
                "На хосте один раз:\n"
                f"<code>sudo bash {ROOT}/deploy/spawn_ota.sh {zip_path}</code>\n"
                f"<code>{how}</code>",
                parse_mode="HTML",
            )
            return True
        send(
            user_id,
            "OTA запущена вне процесса бота (systemd-run).\n"
            "После старта придёт сообщение: успешно или с ошибкой.\n"
            f"Лог хоста: <code>data/ota/ota_update.log</code>",
            parse_mode="HTML",
        )
        return True
    answer(callback_id)
    return True


def handle_admin_document(
    *,
    user_id: int,
    chat: dict,
    document: dict,
    send: SendFn,
    download_file: Callable[[str, Path], bool],
) -> bool:
    if chat.get("type") != "private":
        return False
    if not is_full_admin(user_id):
        return False
    file_name = document.get("file_name") or "update.zip"
    waiting = is_waiting_ota_doc(user_id)
    if not waiting:
        if str(file_name).lower().endswith(".zip"):
            send(
                user_id,
                "ZIP принят только в режиме OTA.\n"
                "Сначала /admin → Хост → OTA, затем пришлите архив.",
            )
            return True
        return False
    if not str(file_name).lower().endswith(".zip"):
        send(user_id, "Нужен .zip")
        return True
    OTA_DIR.mkdir(parents=True, exist_ok=True)
    dest = OTA_DIR / "incoming.zip"
    file_id = document.get("file_id")
    if not file_id or not download_file(file_id, dest):
        send(user_id, "Не удалось скачать файл")
        return True
    ok, reason = validate_ota_zip(dest)
    if not ok:
        send(user_id, f"ZIP отклонён: {reason}")
        return True
    set_session(user_id, "ota_ready", zip_name="incoming.zip")
    send(
        user_id,
        f"ZIP ок ({dest.stat().st_size} bytes). Применить?",
        reply_markup={
            "inline_keyboard": [
                [{"text": "CONFIRM apply", "callback_data": "adm:ota:apply:incoming.zip"}],
                [{"text": "Отмена", "callback_data": "adm:menu:host"}],
            ]
        },
    )
    return True
