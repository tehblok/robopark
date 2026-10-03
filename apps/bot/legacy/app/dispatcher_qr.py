"""QR-код YASADR для робота (диспетчер-бот)."""

from __future__ import annotations

import io

import qrcode

YASADR_PREFIX = "YASADR"
YASADR_ZERO_PAD = 7
YASADR_SUFFIX_LEN = 4


def yasadr_code(rover: str) -> str:
    num = int(rover.lstrip("a"))
    return f"{YASADR_PREFIX}{'0' * YASADR_ZERO_PAD}{num:04d}"


def render_yasadr_qr_png(rover: str) -> bytes:
    code = yasadr_code(rover)
    qr = qrcode.QRCode(version=1, box_size=10, border=1)
    qr.add_data(code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
