"""Short-lived, single-flight cache for Emergency robot payloads."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from robopark_api.services import emergency_client
from robopark_api.services import platform_settings as settings_svc

PAYLOAD_CACHE_TTL_SECONDS = 5.0


@dataclass
class _Flight:
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None


_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_flights: dict[str, _Flight] = {}


def invalidate_vin(vin: str) -> None:
    with _lock:
        _cache.pop(vin, None)


def clear_cache_for_tests() -> None:
    with _lock:
        _cache.clear()
        _flights.clear()


def _finish_flight(
    vin: str,
    flight: _Flight,
    *,
    result: dict[str, Any] | None = None,
    error: BaseException | None = None,
) -> None:
    with _lock:
        flight.result = result
        flight.error = error
        if result is not None:
            _cache[vin] = (time.monotonic(), result)
        _flights.pop(vin, None)
        flight.done.set()


def get_robot_payload(*, db: Session, vin: str) -> dict[str, Any]:
    now = time.monotonic()
    with _lock:
        cached = _cache.get(vin)
        if cached is not None and now - cached[0] < PAYLOAD_CACHE_TTL_SECONDS:
            payload = cached[1]
            flight = None
            is_leader = False
        else:
            if cached is not None:
                _cache.pop(vin, None)
            flight = _flights.get(vin)
            is_leader = flight is None
            if is_leader:
                flight = _Flight()
                _flights[vin] = flight
            payload = None

    if payload is not None:
        settings_svc.touch_keepalive_ring(db, vin)
        return payload

    assert flight is not None
    if not is_leader:
        flight.done.wait()
        if flight.error is not None:
            raise flight.error
        assert flight.result is not None
        return flight.result

    try:
        cookie = settings_svc.get_emergency_cookie(db) or ""
        payload = emergency_client.fetch_robot_payload(cookie=cookie, vin=vin)
        settings_svc.set_emergency_cookie_valid(db, True)
        settings_svc.touch_keepalive_ring(db, vin)
    except emergency_client.EmergencyAuthError as exc:
        invalidate_vin(vin)
        settings_svc.set_emergency_cookie_valid(db, False)
        _finish_flight(vin, flight, error=exc)
        raise
    except BaseException as exc:
        _finish_flight(vin, flight, error=exc)
        raise

    _finish_flight(vin, flight, result=payload)
    return payload
