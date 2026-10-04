"""Process-independent terminal admission rules; monotonic session deadlines."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class TerminalSession:
    request: object
    hard_deadline: float
    last_input: float
    last_lease: float
    detached_deadline: float | None
    attachment_id: str | None = None
    reason: str | None = None
    input_bytes: int = 0
    output_bytes: int = 0

    def public(self):
        return {
            "id": self.request.session_id,
            "profile": self.request.profile,
            "state": "ended"
            if self.reason
            else "active"
            if self.attachment_id
            else "detached",
            "termination_reason": self.reason,
            "input_bytes": self.input_bytes,
            "output_bytes": self.output_bytes,
        }


class TerminalRegistry:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.sessions = {}

    def admit(self, request):
        old = self.sessions.get(request.session_id)
        if old:
            if old.request != request:
                raise ValueError("terminal_request_conflict")
            return old, False
        if len(self.sessions) >= 4096:
            raise ValueError("terminal_history_limit")
        live = [row for row in self.sessions.values() if not row.reason]
        if len(live) >= 2 or any(
            row.request.profile == request.profile for row in live
        ):
            raise ValueError("terminal_session_limit")
        now = self.clock()
        row = TerminalSession(
            request, now + request.remaining_seconds, now, now, now + 15
        )
        self.sessions[request.session_id] = row
        return row, True

    def active(self, session_id, attachment_id=None):
        row = self.sessions.get(session_id)
        if not row or row.reason or any(key == session_id for key, _ in self.expired()):
            raise ValueError("terminal_session_ended")
        if attachment_id is not None and attachment_id != row.attachment_id:
            raise ValueError("terminal_attachment_changed")
        return row

    def attach(self, session_id, attachment_id):
        row = self.active(session_id)
        if not isinstance(attachment_id, str) or not 1 <= len(attachment_id) <= 64:
            raise ValueError("terminal_invalid_attachment")
        if row.attachment_id is not None:
            raise ValueError("terminal_already_attached")
        row.attachment_id = attachment_id
        row.detached_deadline = None
        row.last_lease = self.clock()

    def detach(self, session_id, attachment_id):
        row = self.sessions.get(session_id)
        if row and row.attachment_id == attachment_id and not row.reason:
            row.attachment_id = None
            row.detached_deadline = self.clock() + 15

    def renew(self, session_id, attachment_id):
        self.active(session_id, attachment_id).last_lease = self.clock()

    def input_seen(self, session_id, attachment_id):
        self.active(session_id, attachment_id).last_input = self.clock()

    def finish(self, session_id, reason):
        row = self.sessions[session_id]
        if not row.reason:
            row.reason = reason
            row.attachment_id = None
            # Retention follows first completion, not admission. A repeated
            # finish is an idempotent replay and must not make an old row recent.
            self.sessions[session_id] = self.sessions.pop(session_id)

    def expired(self):
        now = self.clock()
        result = []
        for session_id, row in self.sessions.items():
            if row.reason:
                continue
            reason = (
                "expired"
                if now >= row.hard_deadline
                else "disconnected"
                if row.detached_deadline is not None and now >= row.detached_deadline
                else "lease_expired"
                if now - row.last_lease >= 20
                else "idle_timeout"
                if now - row.last_input >= 600
                else None
            )
            if reason:
                result.append((session_id, reason))
        return result
