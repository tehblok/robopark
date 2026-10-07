"""Bounded YASADR QR rendering for native Telegram commands."""

from __future__ import annotations

import io
import re

import qrcode

YASADR_PREFIX = "YASADR"
YASADR_ZERO_PAD = 7
_ROBOT_RE = re.compile(r"^[aа]?([0-9]{1,6})$", re.IGNORECASE)


def _robot_number(robot: str) -> int:
    if not isinstance(robot, str):
        raise TypeError("robot must be a string")
    match = _ROBOT_RE.fullmatch(robot.strip())
    if match is None:
        raise ValueError("invalid_robot")
    return int(match.group(1))


def yasadr_code(robot: str) -> str:
    """Convert a bounded short robot number to the legacy YASADR payload."""
    number = _robot_number(robot)
    return f"{YASADR_PREFIX}{'0' * YASADR_ZERO_PAD}{number:04d}"


def render_robot_qr(robot: str) -> bytes:
    """Return the legacy black-on-white QR as in-memory PNG bytes."""
    code = yasadr_code(robot)
    qr = qrcode.QRCode(version=1, box_size=10, border=1)
    qr.add_data(code)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
