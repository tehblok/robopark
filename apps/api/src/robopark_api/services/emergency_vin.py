"""Normalize robot numbers to Emergency API VIN (YASADR + 11 digits)."""

from __future__ import annotations

import re

_VIN_RE = re.compile(r"^YASADR\d{11}$", re.IGNORECASE)
_DIGITS_RE = re.compile(r"(\d+)")


def normalize_robot_id(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty robot number")

    upper = text.upper()
    if _VIN_RE.match(upper):
        return "YASADR" + upper[-11:]

    if upper.startswith("YASADR"):
        digits = "".join(ch for ch in upper[6:] if ch.isdigit())
        if not digits:
            raise ValueError(f"invalid VIN: {raw}")
        return "YASADR" + digits.zfill(11)[-11:]

    match = _DIGITS_RE.search(text)
    if not match:
        raise ValueError(f"robot number not found in {raw!r}")
    return "YASADR" + match.group(1).zfill(11)[-11:]
