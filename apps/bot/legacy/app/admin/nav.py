"""Admin navigation helpers, cached health, edit-in-place send."""

from __future__ import annotations

import time
from typing import Any, Callable

SendFn = Callable[..., Any]

_health_cache: dict[str, Any] | None = None
_health_cache_ts: float = 0.0
HEALTH_CACHE_TTL = 3.0


def cached_health_snapshot() -> dict[str, Any]:
    global _health_cache, _health_cache_ts
    now = time.time()
    if _health_cache is not None and now - _health_cache_ts < HEALTH_CACHE_TTL:
        return dict(_health_cache)
    from store.runtime import health_snapshot

    _health_cache = health_snapshot()
    _health_cache_ts = now
    return dict(_health_cache)


def invalidate_health_cache() -> None:
    global _health_cache, _health_cache_ts
    _health_cache = None
    _health_cache_ts = 0.0


def back_home_row() -> list[dict[str, str]]:
    return [{"text": "🏠 Главная", "callback_data": "adm:home"}]


def back_row(callback_data: str, label: str = "« Назад") -> list[dict[str, str]]:
    return [{"text": label, "callback_data": callback_data}]


def rows_with_nav(rows: list[list[dict[str, str]]], *nav: list[dict[str, str]]) -> dict:
    out = list(rows)
    for row in nav:
        out.append(row)
    return {"inline_keyboard": out}


def pair_row(left: dict[str, str], right: dict[str, str]) -> list[dict[str, str]]:
    return [left, right]


def make_edit_send(
    user_id: int,
    message_id: int | None,
    send_new: SendFn,
    edit_msg: Callable[..., bool],
) -> SendFn:
    """Prefer editMessageText on the callback message; fall back to new message."""

    def send(
        uid: int,
        body: str,
        *,
        reply_markup: dict | None = None,
        parse_mode: str | None = None,
        **kwargs: Any,
    ) -> Any:
        if message_id and int(uid) == int(user_id):
            if edit_msg(
                uid,
                message_id,
                body,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            ):
                return None
        return send_new(
            uid,
            body,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            **kwargs,
        )

    return send


def status_line() -> str:
    h = cached_health_snapshot()
    disp = "⏸" if h.get("dispatcher_paused") else "▶️"
    send = "⏸" if h.get("send_paused") else "▶️"
    alive = "🟢" if h.get("pid_alive") else "🔴"
    msk = h.get("msk_now") or "—"
    return (
        f"{alive} bot · disp {disp} · send {send} · "
        f"<code>{h.get('profile', '?')}</code> · MSK <code>{msk}</code>"
    )
