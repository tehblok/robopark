from __future__ import annotations

from typing import Any

from robopark_api.services.emergency_vin import short_robot_number

WHEEL_SLOTS = ("fl", "ml", "rl", "fr", "mr", "rr")


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, dict):
        for key in ("value", "speed", "mps", "kmh"):
            inner = _as_float(value.get(key))
            if inner is not None:
                return inner
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _position(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("position")
    if not isinstance(raw, dict):
        raw = {}
    heading = _as_float(raw.get("yaw"))
    if heading is None:
        heading = _as_float(raw.get("heading"))
    if heading is None:
        heading = _as_float(raw.get("angle"))
    hud = payload.get("robotHudData")
    if heading is None and isinstance(hud, dict):
        heading = _as_float(hud.get("heading"))
    return {
        "lat": _as_float(raw.get("lat")),
        "lon": _as_float(raw.get("lon") if raw.get("lon") is not None else raw.get("lng")),
        "heading_deg": heading,
    }


def _wheel_slots(raw: Any) -> list[str]:
    if raw in (None, False, "", [], {}):
        return []
    slots: list[str] = []
    items = raw if isinstance(raw, list) else [raw]
    mapped = False
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, int) and 0 <= item < 6:
            slots.append(WHEEL_SLOTS[item])
            mapped = True
            continue
        text = str(item).lower() if item is not None else ""
        side_left = "left" in text or text == "l" or text.endswith("_l")
        side_right = "right" in text or text == "r" or text.endswith("_r")
        row = None
        if "front" in text or text.startswith("f"):
            row = "f"
        elif "rear" in text or "back" in text or (text.startswith("r") and "right" not in text):
            row = "r"
        elif "mid" in text or "middle" in text:
            row = "m"
        if row and side_left:
            slots.append(f"{row}l")
            mapped = True
        elif row and side_right:
            slots.append(f"{row}r")
            mapped = True
    if mapped:
        out: list[str] = []
        for slot in WHEEL_SLOTS:
            if slot in slots and slot not in out:
                out.append(slot)
        return out
    return ["body"]


def parse_emergency_snapshot(payload: dict[str, Any], *, vin: str) -> dict[str, Any]:
    batteries = payload.get("batteriesStatus")
    charge = None
    if isinstance(batteries, dict):
        charge = _as_float(batteries.get("chargePercents"))
    pos = _position(payload)
    return {
        "vin": vin,
        "short_number": short_robot_number(vin),
        "online": _as_bool(payload.get("isOnline")),
        "speed": _as_float(payload.get("velocity")),
        "charge_percent": charge,
        "lat": pos["lat"],
        "lon": pos["lon"],
        "heading_deg": pos["heading_deg"],
        "wheels_fault": _wheel_slots(payload.get("wheelsBroken")),
    }
