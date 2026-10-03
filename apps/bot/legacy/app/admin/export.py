"""Snapshot export of the running bot (code + ops data, no secrets)."""

from __future__ import annotations

import json
import socket
import threading
import zipfile
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Callable

from paths import ROOT
from store.roles import is_full_admin

SendFn = Callable[..., Any]
AnswerFn = Callable[..., Any]
SendDocumentFn = Callable[..., bool]

OTA_DIR = ROOT / "data" / "ota"
MAX_EXPORT_BYTES = 50 * 1024 * 1024  # Telegram Bot API document limit
PACKAGE_MARKER = "dispatcher_bot.py"
AI_SPEC_NAME = "AI_SPEC.md"
SNAPSHOT_ROOT = "tracker-report-snapshot"

# Code tree (relative to ROOT)
CODE_DIRS = ("app", "deploy", "scripts", "config", "docs", "tests", "vendor")
CODE_FILES = (
    "install.sh",
    "update.sh",
    "ОБНОВИТЬ.sh",
    "ПЕРЕУСТАНОВИТЬ.sh",
    "requirements.txt",
    "requirements-flex.txt",
    "README.md",
    "AGENTS.md",
    "Makefile",
    "pytest.ini",
)
CURSOR_RULES_DIR = Path(".cursor") / "rules"

# Operational data safe to share (structure for dev baseline)
DATA_FILES = (
    "locations.json",
    "locations.sidecar.json",
    "roles.json",
    "roles.sidecar.json",
    "dispatcher_users.json",
    "dispatcher_users.sidecar.json",
    "broadcasts.json",
    "schedules.json",
    "sk_campaigns.json",
)

PROFILE_FILE = ".telegram_profile"

FORBIDDEN_ZIP_SUFFIXES = (
    "secrets.env",
    "credentials.py",
    "telegram_bot_token.previous",
    ".env",
)

FORBIDDEN_ZIP_PARTS = (
    "venv/",
    "logs/",
    "__pycache__/",
    ".git/",
    "data/ota/",
    "dist/",
)

_export_lock = threading.Lock()


def _should_skip_path(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return True
    name = rel.name
    if name == "credentials.py":
        return True
    if name == ".DS_Store":
        return True
    if name.endswith((".pyc", ".pyo")):
        return True
    posix = rel.as_posix()
    for frag in FORBIDDEN_ZIP_PARTS:
        if frag.rstrip("/") in parts or posix.startswith(frag):
            return True
    for forbidden in FORBIDDEN_ZIP_SUFFIXES:
        if name == forbidden or name.endswith(f"/{forbidden}"):
            return True
    if rel.match("docs/plans/*"):
        return True
    return False


def _manifest() -> dict[str, Any]:
    profile = "prod"
    try:
        from telegram_chats import resolve_profile

        profile = resolve_profile()
    except Exception:
        pass
    health: dict[str, Any] = {}
    try:
        from store.runtime import health_snapshot

        health = health_snapshot()
    except Exception:
        pass
    included_data = [f for f in DATA_FILES if (ROOT / "data" / f).is_file()]
    return {
        "kind": "working-bot-snapshot",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hostname": socket.gethostname(),
        "profile": profile,
        "install_root": str(ROOT),
        "health": health,
        "included_data": included_data,
        "excluded_secrets": list(FORBIDDEN_ZIP_SUFFIXES),
        "note": (
            "Снимок работающей установки для разработки в Cursor. "
            "Внутри docs/AI_SPEC.md — живая спека. Секреты не включены."
        ),
        "ai_spec": "docs/AI_SPEC.md",
    }


CURSOR_STARTER_PROMPT = """Ты Cursor-агент в актуальном снимке tracker-report (Yandex Tracker + Telegram-бот на Armbian). История чата пустая — это код с боевого хоста.

Сначала прочитай и следуй:
1) docs/AI_SPEC.md — что / как / почему, двое в Cursor
2) AGENTS.md — жёсткие запреты
3) docs/UPDATES.md — как выкатывать

Правила:
- Python под app/, всегда PYTHONPATH=app, cwd = корень репозитория (рядом data/)
- Не логировать и не коммитить токены. Не затирать data/, secrets.env, .telegram_profile на проде
- У локации key ≠ display_name ≠ tracker_tag
- Админка только Telegram, callbacks только adm:
- Один TELEGRAM_BOT_TOKEN — один getUpdates (dispatcher)
- После изменения поведения обнови docs/AI_SPEC.md и дату «Обновлено»
- Перед сдачей: make test. На хост — ./deploy/pack_release.sh и /admin → OTA
- Не добавляй веб-админку. Не коммить и не пушь, пока я не попрошу

Токенов в архиве нет: cp config/env.example data/secrets.env && chmod 600, свои тестовые TRACKER_TOKEN и TELEGRAM_BOT_TOKEN. Затем make test.

Жди мою задачу (баг или фича) и работай по spec."""


def cursor_starter_prompt() -> str:
    return CURSOR_STARTER_PROMPT.strip() + "\n"


def _usage_readme() -> str:
    prompt = cursor_starter_prompt()
    return f"""Снимок tracker-report (рабочая установка)
=====================================

Актуальный код с хоста + спека для Cursor (docs/AI_SPEC.md).
Секреты (токены) намеренно не включены.

Как открыть у второго разработчика (Cursor):

1) Распакуйте ZIP в отдельную папку (не на production).
2) Cursor → Open Folder на эту папку.
3) python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
4) cp config/env.example data/secrets.env && chmod 600 data/secrets.env
   Заполните TRACKER_TOKEN и TELEGRAM_BOT_TOKEN тестовыми значениями.
5) В чат Cursor вставьте промпт из блока ниже (целиком).
6) make test. После правок: обновите docs/AI_SPEC.md,
   ./deploy/pack_release.sh → /admin → OTA.
   Не копируйте data/ и secrets.env на боевой хост.

Промпт для ИИ (скопировать целиком в Cursor):
---------------------------------------------
{prompt}
---------------------------------------------

Манифест: EXPORT_MANIFEST.json
Спека: docs/AI_SPEC.md
Правила Cursor: .cursor/rules/
"""


def export_followup_html(filename: str, size_mb: float, *, on_host: str | None = None) -> str:
    """Telegram HTML after a successful (or local-only) export."""
    prompt_html = escape(cursor_starter_prompt())
    host_line = ""
    if on_host:
        host_line = f"\nФайл на хосте:\n<code>{escape(on_host)}</code>\n"
    return (
        f"✅ Архив готов: <code>{escape(filename)}</code> ({size_mb:.2f} MiB), без секретов.\n"
        f"{host_line}\n"
        "<b>Как начать в Cursor</b>\n"
        "1. Распакуй ZIP в <b>новую</b> папку (не на проде)\n"
        "2. Cursor → Open Folder на эту папку\n"
        "3. <code>python3 -m venv venv && ./venv/bin/pip install -r requirements.txt</code>\n"
        "4. <code>cp config/env.example data/secrets.env && chmod 600 data/secrets.env</code> "
        "— тестовые токены\n"
        "5. Скорми ИИ <b>следующее сообщение целиком</b> (промпт ниже)\n"
        "6. Правки → обновить <code>docs/AI_SPEC.md</code> → <code>make test</code> → OTA. "
        "Свой data/ на хост не копировать.\n\n"
        "<b>Промпт для ИИ — скопируй в Cursor:</b>\n"
        f"<pre>{prompt_html}</pre>"
    )


def _bundled_ai_spec_path() -> Path:
    return Path(__file__).resolve().parent / "AI_SPEC.md"


def ai_spec_text() -> str:
    """Prefer host docs/; else bundled copy under app/ (old OTA did not sync docs/)."""
    host = ROOT / "docs" / AI_SPEC_NAME
    if host.is_file():
        return host.read_text(encoding="utf-8")
    bundled = _bundled_ai_spec_path()
    if bundled.is_file():
        return bundled.read_text(encoding="utf-8")
    return (
        "# AI spec — tracker-report\n\n"
        "На хосте не было docs/AI_SPEC.md (старый OTA не копировал docs/). "
        "Прочитай AGENTS.md. После следующего OTA спека должна появиться.\n"
    )


def _heal_ai_spec_on_host(text: str) -> None:
    dest = ROOT / "docs" / AI_SPEC_NAME
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            dest.write_text(text, encoding="utf-8")
    except OSError:
        pass


def build_working_bot_export(dest: Path | None = None) -> Path:
    """Pack running install into zip; return path to archive."""
    with _export_lock:
        OTA_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = dest or (OTA_DIR / f"working-bot-export-{stamp}.zip")
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()

        spec_text = ai_spec_text()
        _heal_ai_spec_on_host(spec_text)

        prefix = f"{SNAPSHOT_ROOT}/"
        wrote_spec = False
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for dname in CODE_DIRS:
                src_dir = ROOT / dname
                if not src_dir.is_dir():
                    continue
                for path in src_dir.rglob("*"):
                    if not path.is_file():
                        continue
                    rel = path.relative_to(ROOT)
                    if _should_skip_path(rel):
                        continue
                    zf.write(path, prefix + rel.as_posix())
                    if rel.as_posix() == f"docs/{AI_SPEC_NAME}":
                        wrote_spec = True

            for fname in CODE_FILES:
                path = ROOT / fname
                if path.is_file():
                    rel = Path(fname)
                    if not _should_skip_path(rel):
                        zf.write(path, prefix + fname)

            rules_dir = ROOT / CURSOR_RULES_DIR
            if rules_dir.is_dir():
                for path in rules_dir.rglob("*"):
                    if not path.is_file():
                        continue
                    if path.suffix not in (".mdc", ".md"):
                        continue
                    rel = path.relative_to(ROOT)
                    zf.write(path, prefix + rel.as_posix())

            if not wrote_spec:
                zf.writestr(prefix + f"docs/{AI_SPEC_NAME}", spec_text)

            for fname in DATA_FILES:
                path = ROOT / "data" / fname
                if path.is_file():
                    zf.write(path, prefix + "data/" + fname)

            profile = ROOT / PROFILE_FILE
            if profile.is_file():
                zf.write(profile, prefix + PROFILE_FILE)

            manifest = _manifest()
            zf.writestr(
                prefix + "EXPORT_MANIFEST.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            zf.writestr(prefix + "КАК_ИСПОЛЬЗОВАТЬ.txt", _usage_readme())

        ok, reason = validate_export_zip(out)
        if not ok:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"export validation failed: {reason}")
        return out


def validate_export_zip(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "file missing"
    size = path.stat().st_size
    if size > MAX_EXPORT_BYTES:
        return False, f"too large ({size} > {MAX_EXPORT_BYTES})"
    if size < 200:
        return False, "too small"
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            for name in names:
                if name.startswith("/") or ".." in Path(name).parts:
                    return False, f"zip-slip: {name}"
                low = name.lower()
                for forbidden in FORBIDDEN_ZIP_SUFFIXES:
                    if forbidden in low and not low.endswith("secrets.env.example"):
                        if name.endswith(forbidden) or f"/{forbidden}" in name:
                            return False, f"forbidden: {name}"
            if not any(
                Path(n).name == PACKAGE_MARKER for n in names if not n.endswith("/")
            ):
                return False, f"missing marker {PACKAGE_MARKER}"
            if not any(n.endswith("EXPORT_MANIFEST.json") for n in names):
                return False, "missing EXPORT_MANIFEST.json"
            if not any(n.endswith(f"docs/{AI_SPEC_NAME}") or n.endswith(AI_SPEC_NAME) for n in names):
                return False, f"missing docs/{AI_SPEC_NAME}"
    except zipfile.BadZipFile:
        return False, "bad zip"
    return True, "ok"


def _restore_host_menu(send: SendFn, user_id: int) -> None:
    from admin.menu import host_menu_keyboard, host_menu_text

    try:
        send(
            user_id,
            host_menu_text(),
            parse_mode="HTML",
            reply_markup=host_menu_keyboard(),
        )
    except Exception:
        pass


def _export_worker(
    user_id: int,
    send: SendFn,
    send_document: SendDocumentFn | None,
    send_new: SendFn | None = None,
) -> None:
    post = send_new or send
    try:
        path = build_working_bot_export()
        size = path.stat().st_size
        size_mb = size / (1024 * 1024)
        caption = (
            f"Архив tracker-report для Cursor\n"
            f"{path.name}\n"
            f"{size_mb:.2f} MiB · без секретов · docs/AI_SPEC.md"
        )
        _restore_host_menu(send, user_id)
        from tg_io import lock

        with lock:
            if send_document and size <= MAX_EXPORT_BYTES:
                if send_document(user_id, path, caption=caption):
                    post(
                        user_id,
                        export_followup_html(path.name, size_mb),
                        parse_mode="HTML",
                    )
                    return
            post(
                user_id,
                export_followup_html(path.name, size_mb, on_host=str(path))
                + "\n\n⚠️ В Telegram файл не ушёл "
                f"(лимит {MAX_EXPORT_BYTES // (1024 * 1024)} MiB или ошибка отправки) — "
                "заберите ZIP с хоста.",
                parse_mode="HTML",
            )
    except Exception as e:
        _restore_host_menu(send, user_id)
        post(user_id, f"❌ Ошибка выгрузки: <code>{e}</code>", parse_mode="HTML")


def handle_export_callback(
    *,
    user_id: int,
    parts: list[str],
    callback_id: str,
    send: SendFn,
    answer: AnswerFn,
    send_document: SendDocumentFn | None = None,
    send_new: SendFn | None = None,
) -> bool:
    if not is_full_admin(user_id):
        answer(callback_id, "Нет прав")
        return True

    action = parts[2] if len(parts) > 2 else "menu"

    if action in ("menu", "ask"):
        answer(callback_id)
        text = (
            "<b>📥 Архив бота для Cursor</b>\n\n"
            "ZIP с <b>текущим кодом</b> хоста, операционными JSON "
            "(локации, роли, люди, рассылки, СК, расписание) "
            "и живой спекой <code>docs/AI_SPEC.md</code> + правила "
            "<code>.cursor/rules</code>.\n\n"
            "Распаковать → Open Folder в Cursor → читать спеку → пилить обновления.\n\n"
            "<b>Не включается:</b> secrets.env, credentials.py, токены, логи, venv.\n\n"
            "Подтвердите выгрузку:"
        )
        kb = {
            "inline_keyboard": [
                [{"text": "CONFIRM архив", "callback_data": "adm:export:run"}],
                [{"text": "Отмена", "callback_data": "adm:menu:host"}],
            ]
        }
        send(user_id, text, parse_mode="HTML", reply_markup=kb)
        return True

    if action == "run":
        answer(callback_id, "Сборка…")
        send(
            user_id,
            "⏳ Собираю снимок установки… Это может занять до минуты.",
        )
        threading.Thread(
            target=_export_worker,
            args=(user_id, send, send_document, send_new or send),
            daemon=True,
            name="export-working-bot",
        ).start()
        return True

    answer(callback_id)
    return True
