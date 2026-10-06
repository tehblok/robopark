"""Read-only SDC Inventory contracts; never a model-selected URL or credential.

The deployment's corporate Tracker OAuth is also accepted by SDC Inventory
when that service grants it access. No credentials from a developer laptop are
copied into the deployment. Upstream ACL failures remain explicit.
"""

import json
import re
import threading
import time
from datetime import UTC, datetime

import httpx

from robopark_api.services.emergency_client import _ssl_context

BASE_URL = "https://inventory.sdc.yandex-team.ru/api/v1"
MAX_RESPONSE_BYTES = 1024 * 1024
_SLOTS = threading.BoundedSemaphore(4)
_CARD_FIELDS = (
    "name",
    "vin",
    "model",
    "modification",
    "port",
    "storage",
    "detailed_location",
    "base_point",
    "shipping_point",
    "fleet",
    "fleet_id",
    "lifecycle_status",
    "state",
)
_REFERENCE_FIELDS = ("id", "name", "title", "code", "slug")


class InventoryError(Exception):
    pass


def robot_name(value: str) -> str:
    text = value.strip().lower().replace("а", "a")
    if re.fullmatch(r"yasadr[0-9]{11}", text):
        return "a" + (text[6:].lstrip("0") or "0")
    if re.fullmatch(r"a?[0-9]{1,11}", text):
        return text if text.startswith("a") else "a" + text
    raise ValueError("invalid_robot_number")


def _get(token: str, path: str, params=None):
    if not _SLOTS.acquire(blocking=False):
        raise InventoryError("sdc_inventory_busy")
    try:
        with httpx.Client(
            timeout=10, follow_redirects=False, trust_env=False, verify=_ssl_context()
        ) as client:
            deadline = time.monotonic() + 20
            with client.stream(
                "GET",
                BASE_URL + path,
                params=params,
                headers={"Authorization": "OAuth " + token, "Accept": "application/json"},
            ) as response:
                if response.status_code in {401, 403}:
                    raise InventoryError("sdc_inventory_access_denied")
                if response.status_code == 404:
                    raise InventoryError("sdc_inventory_not_found")
                if response.status_code != 200:
                    raise InventoryError("sdc_inventory_upstream_error")
                chunks, size = [], 0
                for part in response.iter_bytes(chunk_size=16384):
                    size += len(part)
                    if size > MAX_RESPONSE_BYTES or time.monotonic() > deadline:
                        raise InventoryError("sdc_inventory_response_too_large")
                    chunks.append(part)
                return json.loads(b"".join(chunks))
    except (httpx.HTTPError, ValueError, RecursionError, OSError):
        raise InventoryError("sdc_inventory_unavailable") from None
    finally:
        _SLOTS.release()


def _value(value):
    if isinstance(value, dict):
        return {key: _scalar(value[key]) for key in _REFERENCE_FIELDS if key in value}
    return _scalar(value)


def _scalar(value):
    if value is None or type(value) in {bool, int, float}:
        return value
    return value[:500] if isinstance(value, str) else None


def _evidence(name):
    return {
        "source": "SDC Inventory",
        "robot_name": name,
        "checked_at": datetime.now(UTC).isoformat(),
        "physical_inspection": "not_performed",
    }


def fetch_card(token, name):
    name = robot_name(name)
    payload = _get(token, f"/rovers/{name}/")
    if not isinstance(payload, dict) or str(payload.get("name", "")).lower() != name:
        raise InventoryError("sdc_inventory_response_invalid")
    return {
        **_evidence(name),
        "robot": {key: _value(payload[key]) for key in _CARD_FIELDS if key in payload},
    }


def fetch_installations(token, name, *, offset=0, limit=50, slot=None):
    name = robot_name(name)
    params = {"rover": name, "limit": limit, "offset": offset}
    if slot:
        params["slot"] = slot
    payload = _get(token, "/installations/", params)
    total = None
    if isinstance(payload, dict):
        rows = payload.get("results")
        count = payload.get("count")
        total = count if type(count) is int and count >= 0 else None
        has_more = bool(payload.get("next"))
    else:
        rows, has_more = payload, False
    if not isinstance(rows, list) or len(rows) > limit:
        raise InventoryError("sdc_inventory_response_invalid")
    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rover = row.get("rover")
        if isinstance(rover, dict):
            rover = rover.get("name")
        if not rover and isinstance(row.get("rover_obj"), dict):
            rover = row["rover_obj"].get("name")
        # Do not trust a silently ignored upstream filter to enforce scope.
        if str(rover).lower() != name or (slot and row.get("slot") != slot):
            continue
        unit = row.get("hardware_unit")
        unit = unit if isinstance(unit, dict) else {}
        items.append(
            {
                **{
                    key: _value(row[key])
                    for key in ("id", "slot", "status", "created_at")
                    if key in row
                },
                "rover": name,
                "hardware_unit": {
                    key: _value(unit[key])
                    for key in (
                        "id",
                        "name",
                        "model",
                        "serial_number",
                        "serial",
                        "barcode",
                        "inventory_number",
                    )
                    if key in unit
                },
            }
        )
    if total is not None:
        has_more = has_more or offset + len(rows) < total
    elif not isinstance(payload, dict) or "next" not in payload:
        has_more = len(rows) == limit
    if has_more and not rows:
        raise InventoryError("sdc_inventory_response_invalid")
    return {
        **_evidence(name),
        "items": items,
        "offset": offset,
        "limit": limit,
        "upstream_total": total,
        "excluded_items": len(rows) - len(items),
        "next_offset": offset + len(rows) if has_more else None,
        "page_complete": len(rows) == len(items),
        "complete": not has_more and offset == 0 and len(rows) == len(items),
    }
