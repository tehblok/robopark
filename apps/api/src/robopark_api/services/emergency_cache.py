"""Short-lived, single-flight cache for Emergency robot payloads."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from robopark_api.config import get_settings
from robopark_api.db import release_request_session
from robopark_api.services import emergency_client, emergency_vin, reports
from robopark_api.services import platform_settings as settings_svc
from robopark_api.services.live_merge import get_live_merge_store
from robopark_api.services.ops.maintenance import host_maintenance_active

PAYLOAD_CACHE_TTL_SECONDS = 3.0
PAYLOAD_CACHE_MAX_ENTRIES = 512
PAYLOAD_MAX_STALE_SECONDS = 60.0
_MERGE_NS = "emergency.robot"


@dataclass
class _Flight:
    generation: int
    identity: str | None
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None


_lock = threading.Lock()
_cache: OrderedDict[str, tuple[float, str | None, dict[str, Any]]] = OrderedDict()
_flights: dict[tuple[str | None, str], _Flight] = {}
_generation = 0
_http_slots_lock = threading.Lock()
_http_slots: threading.BoundedSemaphore | None = None
_http_slots_limit: int | None = None


def _shared_key(identity: str | None, vin: str) -> str:
    return f"{identity or 'legacy'}:{vin}"


def _http_semaphore() -> threading.BoundedSemaphore:
    global _http_slots, _http_slots_limit
    limit = get_settings().emergency_max_concurrency
    if limit <= 0:
        raise ValueError("emergency_max_concurrency must be positive")
    with _http_slots_lock:
        if _http_slots is None or _http_slots_limit != limit:
            _http_slots = threading.BoundedSemaphore(limit)
            _http_slots_limit = limit
        return _http_slots


def _prune_cache_locked(now: float) -> None:
    expired = [
        vin for vin, cached in _cache.items() if now - cached[0] >= PAYLOAD_MAX_STALE_SECONDS
    ]
    for vin in expired:
        _cache.pop(vin, None)


def _cache_payload_locked(
    vin: str,
    *,
    loaded_at: float,
    identity: str | None,
    payload: dict[str, Any],
) -> None:
    _cache[vin] = (loaded_at, identity, payload)
    _cache.move_to_end(vin)
    while len(_cache) > PAYLOAD_CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


def _shared_loaded_at(mtime: float | None) -> float:
    now = time.monotonic()
    if mtime is None:
        return now
    return now - max(0.0, time.time() - mtime)


def peek_robot_payloads(*, vins: list[str], identity: str | None) -> dict[str, dict[str, Any]]:
    """Read fresh, identity-bound payloads only; never load, wait or refresh.

    Registry misses remain unknown. In-process entries deliberately retain the
    same 3 second lifetime as the detail endpoint; no second telemetry store.
    """
    now = time.monotonic()
    with _lock:
        _prune_cache_locked(now)
        result = {}
        for vin in vins:
            cached = _cache.get(vin)
            if (
                cached is not None
                and cached[1] == identity
                and now - cached[0] < PAYLOAD_CACHE_TTL_SECONDS
            ):
                result[vin] = cached[2]
                _cache.move_to_end(vin)
        return result


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
    global _http_slots, _http_slots_limit
    with _lock:
        _flights.clear()
    with _http_slots_lock:
        _http_slots = None
        _http_slots_limit = None
    clear_cache()


def _finish_flight(
    vin: str,
    flight: _Flight,
    *,
    result: dict[str, Any] | None = None,
    error: BaseException | None = None,
    loaded_at: float | None = None,
) -> None:
    with _lock:
        flight.result = result
        flight.error = error
        if result is not None and flight.generation == _generation:
            _cache_payload_locked(
                vin,
                loaded_at=loaded_at if loaded_at is not None else time.monotonic(),
                identity=flight.identity,
                payload=result,
            )
        flight_key = (flight.identity, vin)
        if _flights.get(flight_key) is flight:
            _flights.pop(flight_key, None)
        flight.done.set()


def _flight_is_current(flight: _Flight) -> bool:
    with _lock:
        return flight.generation == _generation


def get_robot_payload(
    *,
    db: Session,
    vin: str,
    probe: tuple[str | None, str | None] | None = None,
) -> dict[str, Any]:
    """Fetch using one cookie/identity capture when a caller already has it."""
    cookie, identity = probe or settings_svc.get_emergency_cookie_probe(db)
    now = time.monotonic()
    merge = get_live_merge_store()
    stale: tuple[float, str | None, dict[str, Any]] | None = None
    with _lock:
        _prune_cache_locked(now)
        generation = _generation
        cached = _cache.get(vin)
        if (
            cached is not None
            and cached[1] == identity
            and now - cached[0] < PAYLOAD_CACHE_TTL_SECONDS
        ):
            payload = cached[2]
            _cache.move_to_end(vin)
            flight = None
            is_leader = False
        else:
            if cached is not None:
                if cached[1] == identity and now - cached[0] < PAYLOAD_MAX_STALE_SECONDS:
                    stale = cached
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
                    _cache_payload_locked(
                        vin,
                        loaded_at=_shared_loaded_at(merge.result_mtime(_MERGE_NS, shared_key)),
                        identity=identity,
                        payload=blob,
                    )
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
    payload_loaded_at: float | None = None
    error: BaseException | None = None
    try:
        try:
            used_shared_stale = False

            def mark_shared_stale() -> None:
                nonlocal used_shared_stale
                used_shared_stale = True

            def load() -> dict[str, Any]:
                release_request_session()
                with _http_semaphore():
                    return emergency_client.fetch_robot_payload(cookie=cookie or "", vin=vin)

            if merge is not None:
                shared_key = _shared_key(identity, vin)
                payload = merge.merge_load(
                    _MERGE_NS,
                    shared_key,
                    PAYLOAD_CACHE_TTL_SECONDS,
                    load,
                    is_current=lambda: _flight_is_current(flight),
                    max_stale_seconds=PAYLOAD_MAX_STALE_SECONDS,
                    stale_if=lambda exc: (
                        isinstance(exc, emergency_client.EmergencyError)
                        and not isinstance(exc, emergency_client.EmergencyAuthError)
                    ),
                    on_stale=mark_shared_stale,
                )
            else:
                payload = load()
            payload_loaded_at = (
                _shared_loaded_at(merge.result_mtime(_MERGE_NS, shared_key))
                if merge is not None
                else time.monotonic()
            )
            if host_maintenance_active():
                return payload
            if used_shared_stale:
                if _flight_is_current(flight):
                    settings_svc.record_emergency_cookie_probe(
                        db,
                        identity=identity,
                        valid=None,
                        status="unavailable",
                        checked_robot=emergency_vin.short_robot_number(vin),
                    )
            elif _flight_is_current(flight) and settings_svc.record_emergency_cookie_probe(
                db,
                identity=identity,
                valid=True,
                vin=vin,
                status="valid",
                checked_robot=emergency_vin.short_robot_number(vin),
            ):
                reports.resolve_open_emergency_cookie_reports(db, expected_identity=identity)
        except emergency_client.EmergencyAuthError:
            if host_maintenance_active():
                raise
            if _flight_is_current(flight) and settings_svc.record_emergency_cookie_probe(
                db,
                identity=identity,
                valid=False,
                status="invalid",
                checked_robot=emergency_vin.short_robot_number(vin),
            ):
                invalidate_vin(vin, identity=identity)
                reports.ensure_open_emergency_cookie_report(
                    db,
                    author=None,
                    expected_identity=identity,
                )
            raise
        except emergency_client.EmergencyError:
            if not host_maintenance_active() and _flight_is_current(flight):
                settings_svc.record_emergency_cookie_probe(
                    db,
                    identity=identity,
                    valid=None,
                    status="unavailable",
                    checked_robot=emergency_vin.short_robot_number(vin),
                )
            raise
    except BaseException as exc:
        if (
            stale is not None
            and isinstance(exc, emergency_client.EmergencyError)
            and not isinstance(exc, emergency_client.EmergencyAuthError)
            and time.monotonic() - stale[0] < PAYLOAD_MAX_STALE_SECONDS
        ):
            payload = stale[2]
            payload_loaded_at = stale[0]
        else:
            error = exc
            raise
    finally:
        _finish_flight(
            vin,
            flight,
            result=payload if error is None else None,
            error=error,
            loaded_at=payload_loaded_at,
        )

    assert payload is not None
    return payload
