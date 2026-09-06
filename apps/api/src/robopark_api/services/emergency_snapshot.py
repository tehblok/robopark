from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from robopark_api.services.diagnostic_rules import match_diagnostic_events
from robopark_api.services.emergency_vin import short_robot_number

WHEEL_SLOTS = ("fl", "ml", "rl", "fr", "mr", "rr")


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, dict):
        for key in (
            "value",
            "speed",
            "mps",
            "kmh",
            "chargePercents",
            "chargePercentage",
            "charge",
            "percent",
            "percents",
            "usedPercents",
            "usagePercents",
            "level",
        ):
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
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on", "online"}:
            return True
        if text in {"false", "0", "no", "off", "offline"}:
            return False
    return None


def _as_label(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        for key in ("label", "name", "mode", "status", "state", "type", "value"):
            inner = _as_label(value.get(key))
            if inner is not None:
                return inner
    return None


def _status_ok(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value > 0
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return None
        if text in {"ok", "online", "good", "true", "connected", "active", "1"}:
            return True
        return text not in {
            "fail",
            "failed",
            "offline",
            "bad",
            "false",
            "error",
            "0",
            "none",
            "n/a",
        }
    if isinstance(value, dict):
        for key in ("ok", "isOk", "online", "connected", "active", "healthy"):
            if key in value:
                return _status_ok(value.get(key))
        for key in ("status", "state", "value", "level"):
            if key in value:
                return _status_ok(value.get(key))
    return None


def _status_label(value: Any, *, fallback: str) -> str | None:
    if value is None:
        return None
    label = _as_label(value)
    if label is not None:
        return label.upper() if len(label) <= 8 else label
    ok = _status_ok(value)
    if ok is True:
        return fallback
    if ok is False:
        return "OFF"
    return fallback


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


_WHEEL_ALIASES = {
    "fl": "fl",
    "lf": "fl",
    "ml": "ml",
    "lm": "ml",
    "rl": "rl",
    "lr": "rl",
    "fr": "fr",
    "rf": "fr",
    "mr": "mr",
    "rm": "mr",
    "rr": "rr",
}


def _connection(payload: dict[str, Any], online: bool | None) -> str | None:
    for key in ("isWired", "wired", "ethernet", "ethernetConnected"):
        if _as_bool(payload.get(key)) is True:
            return "wire"
    lte = payload.get("lte")
    if isinstance(lte, dict):
        ok = _status_ok(lte)
        strengths = [
            _as_float(value) for value in lte.values() if not isinstance(value, (dict, list, bool))
        ]
        if ok is True or any(value is not None and value > 0 for value in strengths):
            return "lte"
        return "wire" if online else None
    if isinstance(lte, str) and "lte" in lte.lower():
        return "lte"
    ok = _status_ok(lte)
    if ok is True:
        return "lte"
    if ok is False:
        return "wire" if online else None
    if online:
        return "wire"
    return None


def _wheel_slots(raw: Any) -> list[str]:
    if raw in (None, False, "", [], {}):
        return []
    if isinstance(raw, dict):
        aliases_present = False
        slots: list[str] = []
        for key, value in raw.items():
            slot = _WHEEL_ALIASES.get(str(key).lower().strip())
            if slot is None:
                continue
            aliases_present = True
            if _as_bool(value) is True:
                slots.append(slot)
        if aliases_present:
            return [slot for slot in WHEEL_SLOTS if slot in slots]
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
        text = str(item).lower().strip() if item is not None else ""
        if text in WHEEL_SLOTS:
            slots.append(text)
            mapped = True
            continue
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


def _battery_pack(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, dict) and "isConnected" in raw and _as_bool(raw.get("isConnected")) is False:
        return None
    return _as_float(raw)


def _battery_levels(payload: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    batteries = payload.get("batteriesStatus")
    if not isinstance(batteries, dict):
        return None, None, None
    overall = _as_float(batteries.get("chargePercents"))
    if overall is None:
        overall = _as_float(batteries.get("chargePercentage"))
    bat1 = _battery_pack(batteries.get("battery1"))
    bat2 = _battery_pack(batteries.get("battery2"))
    if bat1 is None and bat2 is None and overall is not None:
        bat1 = overall
    return bat1, bat2, overall


def _mode_label(payload: dict[str, Any]) -> str | None:
    for key in ("autoMode", "sadrMode", "lifecycleStatus", "profile"):
        value = payload.get(key)
        if isinstance(value, bool):
            if key == "autoMode":
                return "AUTO" if value else "MANUAL"
            continue
        label = _as_label(value)
        if label is not None:
            upper = label.upper()
            if key == "autoMode" and upper in {"TRUE", "1", "YES", "ON"}:
                return "AUTO"
            if key == "autoMode" and upper in {"FALSE", "0", "NO", "OFF"}:
                return "MANUAL"
            return upper if len(upper) <= 10 else label
    return None


def _error_banner(payload: dict[str, Any]) -> str | None:
    for key in (
        "lastCritNotification",
        "lastErrorNotification",
        "errors",
        "panics",
        "notifications",
    ):
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            for item in value:
                text = _as_label(item) if not isinstance(item, str) else item.strip()
                if isinstance(item, dict):
                    text = _as_label(
                        item.get("message") or item.get("text") or item.get("path") or item
                    )
                if text:
                    return text if text.upper().startswith("ERROR") else f"ERROR: {text}"
            continue
        text = _as_label(value)
        if text:
            return text if text.upper().startswith("ERROR") else f"ERROR: {text}"
    return None


def parse_emergency_snapshot(
    payload: dict[str, Any], *, vin: str, db: Session | None = None
) -> dict[str, Any]:
    bat1, bat2, charge = _battery_levels(payload)
    pos = _position(payload)
    icp_raw = payload.get("icp")
    lte_raw = payload.get("lte")
    online = _as_bool(payload.get("isOnline"))
    disk = _as_float(payload.get("disk"))
    if disk is None:
        disk = _as_float(payload.get("diskUsage"))
    return {
        "vin": vin,
        "short_number": short_robot_number(vin),
        "online": online,
        "speed": _as_float(payload.get("velocity")),
        "charge_percent": charge if charge is not None else bat1,
        "battery1_percent": bat1,
        "battery2_percent": bat2,
        "disk_percent": disk,
        "mode": _mode_label(payload),
        "icp_label": _status_label(icp_raw, fallback="ICP"),
        "icp_ok": _status_ok(icp_raw),
        "lte_label": _status_label(lte_raw, fallback="LTE"),
        "lte_ok": _status_ok(lte_raw),
        "connection": _connection(payload, online),
        "error_banner": _error_banner(payload),
        "diagnostic_events": match_diagnostic_events(db, payload) if db is not None else [],
        "lat": pos["lat"],
        "lon": pos["lon"],
        "heading_deg": pos["heading_deg"],
        "wheels_fault": _wheel_slots(payload.get("wheelsBroken")),
    }
