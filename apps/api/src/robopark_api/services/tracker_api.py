"""Retry, rate-limit и лимит параллелизма для Startrek API.

Порт логики bot_otchet ``tracker_api.py``: не больше MAX_INFLIGHT живых
HTTP-запросов, межзапросный интервал, retry на 429/5xx/timeout.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_lock = threading.Lock()
_last_call = 0.0
MAX_INFLIGHT = 2
_inflight = threading.BoundedSemaphore(MAX_INFLIGHT)
_slot_holder_since: dict[int, float] = {}
MIN_INTERVAL_SEC = 0.15
DEFAULT_SLOT_WAIT_SEC = 25.0
DEFAULT_CALL_TIMEOUT_SEC = 25.0
STUCK_SLOT_SEC = 60.0

_RETRY_HINTS = (
    "429",
    "rate limit",
    "too many requests",
    "503",
    "502",
    "504",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "temporarily unavailable",
)
_STATUS_RE = re.compile(r"\b(429|502|503|504)\b")
_executor = ThreadPoolExecutor(max_workers=MAX_INFLIGHT, thread_name_prefix="tracker-api")


def _rate_limit_wait() -> None:
    global _last_call
    with _lock:
        now = time.monotonic()
        delay = MIN_INTERVAL_SEC - (now - _last_call)
    if delay > 0:
        time.sleep(delay)
    with _lock:
        _last_call = time.monotonic()


def _is_retryable(exc: BaseException) -> bool:
    text = str(exc).lower()
    if any(h in text for h in _RETRY_HINTS):
        return True
    return bool(_STATUS_RE.search(str(exc)))


def reset_tracker_slots(*, reason: str = "") -> int:
    released = 0
    while True:
        try:
            _inflight.release()
            released += 1
        except ValueError:
            break
    with _lock:
        _slot_holder_since.clear()
    if released:
        logger.warning(
            "Tracker slots reset (%s released)%s",
            released,
            f": {reason}" if reason else "",
        )
    return released


def _release_stuck_slots() -> None:
    now = time.monotonic()
    with _lock:
        stuck = [tid for tid, since in _slot_holder_since.items() if now - since > STUCK_SLOT_SEC]
    if stuck:
        reset_tracker_slots(reason=f"stuck threads={stuck}")


@contextmanager
def tracker_slot(*, timeout: float = DEFAULT_SLOT_WAIT_SEC) -> Iterator[None]:
    _release_stuck_slots()
    if not _inflight.acquire(timeout=max(1.0, timeout)):
        _release_stuck_slots()
        if not _inflight.acquire(timeout=2.0):
            raise TimeoutError("Tracker API slot wait timed out")
    tid = threading.get_ident()
    with _lock:
        _slot_holder_since[tid] = time.monotonic()
    try:
        _rate_limit_wait()
        yield
    finally:
        with _lock:
            _slot_holder_since.pop(tid, None)
        try:
            _inflight.release()
        except ValueError:
            pass


def call_with_retry(
    fn: Callable[..., T],
    /,
    *args: Any,
    max_attempts: int = 2,
    base_delay: float = 0.5,
    slot_timeout: float = DEFAULT_SLOT_WAIT_SEC,
    call_timeout: float = DEFAULT_CALL_TIMEOUT_SEC,
    **kwargs: Any,
) -> T:
    """Вызов с лимитом параллелизма и таймаутом одного HTTP-вызова.

    Слот держит вызывающий поток; HTTP — в пуле размером MAX_INFLIGHT.
    При таймауте слот не отпускаем, пока worker не завершится (drain),
    иначе orphan-задачи размножаются и упираются в MAX_INFLIGHT.
    """
    last_exc: BaseException | None = None

    for attempt in range(max_attempts):
        try:
            with tracker_slot(timeout=slot_timeout):
                future = _executor.submit(fn, *args, **kwargs)
                try:
                    return future.result(timeout=max(5.0, call_timeout))
                except FuturesTimeout as exc:
                    last_exc = TimeoutError(
                        f"Tracker API call timed out after {call_timeout:.0f}s"
                    )
                    logger.warning("%s (attempt %s) — draining worker", last_exc, attempt + 1)
                    try:
                        return future.result(timeout=max(5.0, call_timeout))
                    except FuturesTimeout:
                        logger.error(
                            "Tracker API call still running after drain; pool worker may be stuck"
                        )
                    except Exception as drain_exc:  # noqa: BLE001
                        logger.warning("Tracker API drain finished with: %s", drain_exc)
                        last_exc = drain_exc
                    if attempt >= max_attempts - 1:
                        raise last_exc from exc
        except TimeoutError as exc:
            last_exc = exc
            if "slot wait" in str(exc).lower():
                raise
            if attempt >= max_attempts - 1:
                raise
            time.sleep(base_delay)
            continue
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_attempts - 1 or not _is_retryable(exc):
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "Tracker API retry %s/%s after %s: %s",
                attempt + 1,
                max_attempts - 1,
                delay,
                exc,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
