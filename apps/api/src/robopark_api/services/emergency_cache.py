"""Short-lived, single-flight cache for Emergency robot payloads."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from robopark_api.db import release_request_session
from robopark_api.services import emergency_client, reports
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.live_merge import get_live_merge_store
from robopark_api.services.ops.maintenance import host_maintenance_active

PAYLOAD_CACHE_TTL_SECONDS = 2.5
_MERGE_NS = "emergency.robot"


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
    merge = get_live_merge_store()
    if merge is not None:
        merge.invalidate(_MERGE_NS, vin)


def clear_cache_for_tests() -> None:
    with _lock:
        _cache.clear()
        _flights.clear()
    merge = get_live_merge_store()
    if merge is not None:
        merge.clear_namespace(_MERGE_NS)


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
    merge = get_live_merge_store()
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
        return payload

    if merge is not None:
        found, blob = merge.try_fresh(_MERGE_NS, vin, PAYLOAD_CACHE_TTL_SECONDS)
        if found:
            with _lock:
                _cache[vin] = (time.monotonic(), blob)
                if flight is not None and is_leader:
                    _flights.pop(vin, None)
                    flight.result = blob
                    flight.done.set()
            return blob

    assert flight is not None
    if not is_leader:
        release_request_session()
        flight.done.wait()
        if flight.error is not None:
            raise flight.error
        assert flight.result is not None
        return flight.result

    payload = None
    error: BaseException | None = None
    try:
        try:

            def load() -> dict[str, Any]:
                cookie = settings_svc.get_emergency_cookie(db) or ""
                release_request_session()
                return emergency_client.fetch_robot_payload(cookie=cookie, vin=vin)

            if merge is not None:
                payload = merge.merge_load(_MERGE_NS, vin, PAYLOAD_CACHE_TTL_SECONDS, load)
            else:
                payload = load()
            if host_maintenance_active():
                return payload
            settings_svc.set_emergency_cookie_valid(db, True)
            settings_svc.touch_keepalive_ring(db, vin)
            reports.resolve_open_emergency_cookie_reports(db)
        except emergency_client.EmergencyAuthError:
            if host_maintenance_active():
                raise
            invalidate_vin(vin)
            settings_svc.set_emergency_cookie_valid(db, False)
            reports.ensure_open_emergency_cookie_report(db, author=None)
            raise
    except BaseException as exc:
        error = exc
        raise
    finally:
        _finish_flight(
            vin,
            flight,
            result=payload if error is None else None,
            error=error,
        )

    assert payload is not None
    return payload
