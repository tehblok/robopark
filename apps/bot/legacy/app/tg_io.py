"""Serialize Telegram Bot API calls across the dispatcher thread and background jobs."""

from __future__ import annotations

import threading

lock = threading.RLock()
