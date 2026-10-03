"""In-memory admin input sessions (token / OTA) with TTL."""

from __future__ import annotations

import time
from typing import Any

_SESSION_TTL = 600.0
_sessions: dict[int, dict[str, Any]] = {}


def set_session(user_id: int, kind: str, **payload: Any) -> None:
    _sessions[int(user_id)] = {
        "kind": kind,
        "created_at": time.time(),
        **payload,
    }


def get_session(user_id: int) -> dict[str, Any] | None:
    uid = int(user_id)
    sess = _sessions.get(uid)
    if not sess:
        return None
    if time.time() - float(sess.get("created_at", 0)) > _SESSION_TTL:
        _sessions.pop(uid, None)
        return None
    return sess


def clear_session(user_id: int) -> None:
    _sessions.pop(int(user_id), None)


def sweep_sessions() -> int:
    """Drop expired sessions (TTL). Returns how many were removed."""
    now = time.time()
    dead = [
        uid
        for uid, sess in list(_sessions.items())
        if now - float(sess.get("created_at", 0) or 0) > _SESSION_TTL
    ]
    for uid in dead:
        _sessions.pop(uid, None)
    return len(dead)
