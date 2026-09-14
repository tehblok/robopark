"""Strict-admin CRUD and safe discovery for the global Emergency readings catalog."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from robopark_api.db import get_db
from robopark_api.deps import require_user
from robopark_api.models import EmergencyReading, EmergencySection, User
from robopark_api.schemas import (
    EmergencyDiscoveredField,
    EmergencyReadingCreate,
    EmergencyReadingOut,
    EmergencyReadingsReorder,
    EmergencyReadingUpdate,
)
from robopark_api.services import (
    audit,
    emergency_cache,
    emergency_client,
    emergency_vin,
    platform_settings,
    rbac,
)
from robopark_api.services.diagnostic_rules import diagnostic_source_parts


def require_readings_admin(user: User = Depends(require_user)) -> User:
    rbac.assert_approved(user)
    if user.role != rbac.RoleSlug.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


router = APIRouter(
    prefix="/admin/emergency-readings",
    tags=["admin-emergency-readings"],
    dependencies=[Depends(require_readings_admin)],
)

ReadingId = Annotated[int, Path(gt=0, le=2**63 - 1)]


def _canonical_no_data(values: list[Any]) -> str:
    return json.dumps(
        values,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _reading_out(reading: EmergencyReading) -> EmergencyReadingOut:
    try:
        no_data_values = json.loads(reading.no_data_json)
        if not isinstance(no_data_values, list):
            no_data_values = []
    except (TypeError, ValueError):
        no_data_values = []
    return EmergencyReadingOut(
        id=reading.id,
        section_id=reading.section_id,
        path=reading.path,
        label=reading.label,
        display_kind=reading.display_kind,
        unit=reading.unit,
        precision=reading.precision,
        enabled_path=reading.enabled_path,
        no_data_values=no_data_values,
        warning_below=reading.warning_below,
        warning_above=reading.warning_above,
        critical_below=reading.critical_below,
        critical_above=reading.critical_above,
        view=reading.view,
        x=reading.x,
        y=reading.y,
        label_direction=reading.label_direction,
        is_enabled=reading.is_enabled,
        sort_order=reading.sort_order,
    )


def _catalog(db: Session) -> list[EmergencyReadingOut]:
    return [
        _reading_out(reading)
        for reading in db.scalars(
            select(EmergencyReading)
            .order_by(EmergencyReading.sort_order, EmergencyReading.id)
            .execution_options(populate_existing=True)
        )
    ]


def _etag(readings: list[EmergencyReadingOut]) -> str:
    serialized = json.dumps(
        [reading.model_dump() for reading in readings],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return '"' + hashlib.sha256(serialized.encode()).hexdigest() + '"'


def _set_catalog_headers(response: Response, readings: list[EmergencyReadingOut]) -> None:
    response.headers["ETag"] = _etag(readings)
    response.headers["Cache-Control"] = "no-store"


@contextmanager
def _write(db: Session) -> Iterator[None]:
    try:
        db.execute(
            update(EmergencyReading)
            .values(sort_order=EmergencyReading.sort_order)
            .execution_options(synchronize_session=False)
        )
        yield
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="emergency_reading_conflict") from None
    except OperationalError as exc:
        db.rollback()
        sqlite_code = getattr(exc.orig, "sqlite_errorcode", 0)
        contended = sqlite_code & 0xFF in {5, 6} or getattr(exc.orig, "pgcode", None) in {
            "40001",
            "40P01",
            "55P03",
        }
        raise HTTPException(
            status_code=409 if contended else 503,
            detail=(
                "emergency_readings_write_conflict"
                if contended
                else "emergency_readings_unavailable"
            ),
        ) from None
    except Exception:
        db.rollback()
        raise


def _get_reading(db: Session, reading_id: int) -> EmergencyReading:
    reading = db.get(EmergencyReading, reading_id, populate_existing=True)
    if reading is None:
        raise HTTPException(status_code=404, detail="emergency_reading_not_found")
    return reading


def _validate_config(db: Session, payload: EmergencyReadingCreate) -> None:
    if db.get(EmergencySection, payload.section_id) is None:
        raise HTTPException(status_code=422, detail="emergency_section_not_found")
    for path in (payload.path, payload.enabled_path):
        if path is None:
            continue
        parts = diagnostic_source_parts(path)
        if parts is None or len(parts) > 12:
            raise HTTPException(status_code=422, detail="invalid_emergency_reading_path")
    if not -(2**63) <= payload.sort_order < 2**63:
        raise HTTPException(status_code=422, detail="invalid_emergency_reading_sort_order")


def _record(db: Session, actor: User, action: str, reading_id: int, **metadata: Any) -> None:
    audit.record(
        db,
        actor=actor,
        action=action,
        target_type="emergency_reading",
        target_id=str(reading_id),
        detail=json.dumps(metadata, sort_keys=True),
    )


@router.get("", response_model=list[EmergencyReadingOut])
def list_readings(response: Response, db: Session = Depends(get_db)) -> list[EmergencyReadingOut]:
    readings = _catalog(db)
    _set_catalog_headers(response, readings)
    return readings


@router.get("/discovered", response_model=list[EmergencyDiscoveredField])
def discover_readings(
    vin: Annotated[str, Query(min_length=1, max_length=64)],
    db: Session = Depends(get_db),
) -> list[EmergencyDiscoveredField]:
    try:
        normalized_vin = emergency_vin.normalize_robot_id(vin)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_robot_number") from exc
    probe = platform_settings.get_emergency_cookie_probe(db)
    if not probe[0]:
        raise HTTPException(status_code=503, detail="emergency_cookie_not_configured")
    try:
        payload = emergency_cache.get_robot_payload(db=db, vin=normalized_vin, probe=probe)
    except emergency_client.EmergencyAuthError as exc:
        raise HTTPException(status_code=403, detail="emergency_cookie_invalid") from exc
    except emergency_client.EmergencyError as exc:
        raise HTTPException(status_code=502, detail="emergency_upstream_error") from exc
    return _discover_scalars(payload)


@router.post("", response_model=EmergencyReadingOut, status_code=201)
def create_reading(
    payload: EmergencyReadingCreate,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_readings_admin),
) -> EmergencyReadingOut:
    _validate_config(db, payload)
    values = payload.model_dump(exclude={"no_data_values"})
    values["no_data_json"] = _canonical_no_data(payload.no_data_values)
    with _write(db):
        reading = EmergencyReading(**values)
        db.add(reading)
        db.flush()
        result = _reading_out(reading)
        _set_catalog_headers(response, _catalog(db))
    _record(
        db,
        actor,
        audit.ACTION_EMERGENCY_READING_CREATED,
        result.id,
        fields=sorted(payload.model_dump()),
        sort_order=result.sort_order,
        is_enabled=result.is_enabled,
    )
    return result


@router.put("/reorder", response_model=list[EmergencyReadingOut])
def reorder_readings(
    payload: EmergencyReadingsReorder,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_readings_admin),
) -> list[EmergencyReadingOut]:
    if if_match is None:
        raise HTTPException(status_code=428, detail="emergency_readings_if_match_required")
    with _write(db):
        current = _catalog(db)
        if if_match != _etag(current):
            raise HTTPException(status_code=409, detail="emergency_readings_catalog_changed")
        by_id = {item.id: item for item in db.scalars(select(EmergencyReading))}
        if len(payload.ids) != len(set(payload.ids)) or set(payload.ids) != set(by_id):
            raise HTTPException(status_code=422, detail="ids_must_include_all_readings_once")
        for sort_order, reading_id in enumerate(payload.ids):
            by_id[reading_id].sort_order = sort_order
        db.flush()
        result = _catalog(db)
        _set_catalog_headers(response, result)
    previous = {reading.id: reading.sort_order for reading in current}
    for reading in result:
        _record(
            db,
            actor,
            audit.ACTION_EMERGENCY_READING_REORDERED,
            reading.id,
            previous_sort_order=previous[reading.id],
            sort_order=reading.sort_order,
        )
    return result


@router.get("/{reading_id}", response_model=EmergencyReadingOut)
def get_reading(
    reading_id: ReadingId,
    response: Response,
    db: Session = Depends(get_db),
) -> EmergencyReadingOut:
    result = _reading_out(_get_reading(db, reading_id))
    _set_catalog_headers(response, _catalog(db))
    return result


@router.patch("/{reading_id}", response_model=EmergencyReadingOut)
def update_reading(
    reading_id: ReadingId,
    payload: EmergencyReadingUpdate,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_readings_admin),
) -> EmergencyReadingOut:
    changes = payload.model_dump(exclude_unset=True)
    with _write(db):
        reading = _get_reading(db, reading_id)
        current = _reading_out(reading).model_dump(exclude={"id"})
        try:
            candidate = EmergencyReadingCreate.model_validate({**current, **changes})
        except ValidationError:
            raise HTTPException(status_code=422, detail="invalid_emergency_reading") from None
        _validate_config(db, candidate)
        changed_fields = sorted(key for key, value in changes.items() if current[key] != value)
        for key in changed_fields:
            if key == "no_data_values":
                reading.no_data_json = _canonical_no_data(changes[key])
            else:
                setattr(reading, key, changes[key])
        db.flush()
        result = _reading_out(reading)
        _set_catalog_headers(response, _catalog(db))
    if changed_fields:
        _record(
            db,
            actor,
            audit.ACTION_EMERGENCY_READING_UPDATED,
            reading_id,
            fields=changed_fields,
        )
    return result


@router.delete("/{reading_id}", status_code=204)
def delete_reading(
    reading_id: ReadingId,
    response: Response,
    db: Session = Depends(get_db),
    actor: User = Depends(require_readings_admin),
) -> Response:
    with _write(db):
        db.delete(_get_reading(db, reading_id))
        db.flush()
        _set_catalog_headers(response, _catalog(db))
    _record(db, actor, audit.ACTION_EMERGENCY_READING_DELETED, reading_id)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def _safe_key(key: str) -> bool:
    folded = key.casefold()
    return (
        "hud" not in folded
        and "sdcoptions" not in folded
        and "cookie" not in folded
        and "token" not in folded
    )


def _example(value: Any) -> tuple[str, str] | None:
    if value is None:
        return "null", "null"
    if type(value) is bool:
        return "boolean", "true" if value else "false"
    if type(value) in {int, float}:
        if type(value) is float and (value != value or value in {float("inf"), float("-inf")}):
            return None
        return "number", json.dumps(value, ensure_ascii=True, allow_nan=False)
    if type(value) is str:
        if len(value) > 128:
            return None
        return "string", value[:80]
    return None


def _discover_scalars(payload: dict[str, Any]) -> list[EmergencyDiscoveredField]:
    result: list[EmergencyDiscoveredField] = []
    visited = 0

    def walk(value: Any, parts: tuple[str, ...]) -> None:
        nonlocal visited
        if len(result) >= 1000 or visited >= 10000 or len(parts) > 12:
            return
        visited += 1
        if type(value) is dict:
            for key in sorted(value):
                if len(result) >= 1000:
                    return
                if type(key) is not str or not _safe_key(key):
                    continue
                next_parts = (*parts, key)
                path = ".".join(next_parts)
                if diagnostic_source_parts(path) is None:
                    continue
                walk(value[key], next_parts)
            return
        if type(value) is list:
            for index, item in enumerate(value):
                if len(result) >= 1000:
                    return
                walk(item, (*parts, str(index)))
            return
        if not parts or (record := _example(value)) is None:
            return
        value_type, example = record
        result.append(
            EmergencyDiscoveredField(path=".".join(parts), value_type=value_type, example=example)
        )

    walk(payload, ())
    return result
