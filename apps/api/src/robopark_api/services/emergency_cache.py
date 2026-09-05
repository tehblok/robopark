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

PAYLOAD_CACHE_TTL_SECONDS = 2.5
_MERGE_NS = "emergency.robot"


@dataclass
class _Flight:
    generation: int
    identity: str | None
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None


_lock = threading.Lock()
_cache: dict[str, tuple[float, str | None, dict[str, Any]]] = {}
_flights: dict[tuple[str | None, str], _Flight] = {}
_generation = 0


def _shared_key(identity: str | None, vin: str) -> str:
    return f"{identity or 'legacy'}:{vin}"


def invalidate_vin(vin: str, *, identity: str | None = None) -> None:
    with _lock:
        cached = _cache.get(vin)
        if cached is not None and (identity is None or cached[1] == identity):
            _cache.pop(vin, None)
    merge = get_live_merge_store()
    if merge is not None:
        merge.invalidate(_MERGE_NS, _shared_key(identity, vin))


def clear_cache() -> None:
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()
        _flights.clear()
    merge = get_live_merge_store()
    if merge is not None:
        merge.clear_namespace(_MERGE_NS)


def clear_cache_for_tests() -> None:
    with _lock:
        _flights.clear()
    clear_cache()


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
        if result is not None and flight.generation == _generation:
            _cache[vin] = (time.monotonic(), flight.identity, result)
        flight_key = (flight.identity, vin)
        if _flights.get(flight_key) is flight:
            _flights.pop(flight_key, None)
        flight.done.set()


def _flight_is_current(flight: _Flight) -> bool:
    with _lock:
        return flight.generation == _generation


def get_robot_payload(*, db: Session, vin: str) -> dict[str, Any]:
    cookie, identity = settings_svc.get_emergency_cookie_probe(db)
    now = time.monotonic()
    merge = get_live_merge_store()
    with _lock:
        generation = _generation
        cached = _cache.get(vin)
        if (
            cached is not None
            and cached[1] == identity
            and now - cached[0] < PAYLOAD_CACHE_TTL_SECONDS
        ):
            payload = cached[2]
            flight = None
            is_leader = False
        else:
            if cached is not None:
                _cache.pop(vin, None)
            flight_key = (identity, vin)
            flight = _flights.get(flight_key)
            is_leader = flight is None
            if is_leader:
                flight = _Flight(generation=generation, identity=identity)
                _flights[flight_key] = flight
            payload = None

    if payload is not None:
        return payload

    if merge is not None:
        shared_key = _shared_key(identity, vin)
        found, blob = merge.try_fresh(_MERGE_NS, shared_key, PAYLOAD_CACHE_TTL_SECONDS)
        if found:
            with _lock:
                if flight is not None and flight.generation == _generation:
                    _cache[vin] = (time.monotonic(), identity, blob)
                if flight is not None and is_leader:
                    flight_key = (identity, vin)
                    if _flights.get(flight_key) is flight:
                        _flights.pop(flight_key, None)
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
                release_request_session()
                return emergency_client.fetch_robot_payload(cookie=cookie or "", vin=vin)

            if merge is not None:
                payload = merge.merge_load(
                    _MERGE_NS,
                    _shared_key(identity, vin),
                    PAYLOAD_CACHE_TTL_SECONDS,
                    load,
                    is_current=lambda: _flight_is_current(flight),
                )
            else:
                payload = load()
            if _flight_is_current(flight) and settings_svc.record_emergency_cookie_probe(
                db,
                identity=identity,
                valid=True,
                vin=vin,
            ):
                reports.resolve_open_emergency_cookie_reports(db, expected_identity=identity)
        except emergency_client.EmergencyAuthError:
            if _flight_is_current(flight) and settings_svc.record_emergency_cookie_probe(
                db,
                identity=identity,
                valid=False,
            ):
                invalidate_vin(vin, identity=identity)
                reports.ensure_open_emergency_cookie_report(
                    db,
                    author=None,
                    expected_identity=identity,
                )
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
