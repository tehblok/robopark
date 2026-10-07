from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

BOT = Path(__file__).resolve().parents[2] / "apps/bot"
sys.path.insert(0, str(BOT))

from native.qr import render_robot_qr, yasadr_code


@pytest.mark.parametrize(
    ("robot", "expected"),
    [
        ("1460", "YASADR00000001460"),
        ("a1460", "YASADR00000001460"),
        ("а1460", "YASADR00000001460"),
        (" A000001 ", "YASADR00000000001"),
        ("А0", "YASADR00000000000"),
        ("123456", "YASADR0000000123456"),
    ],
)
def test_yasadr_code_accepts_bounded_short_robot_forms(robot, expected):
    assert yasadr_code(robot) == expected


@pytest.mark.parametrize(
    "robot",
    [
        "",
        "a",
        "1234567",
        "aa12",
        "b12",
        "YASADR00000001460",
        "12.3",
        "１４６０",
        "[1460]",
        "-1",
    ],
)
def test_yasadr_code_rejects_unbounded_or_ambiguous_forms(robot):
    with pytest.raises(ValueError, match="invalid_robot"):
        yasadr_code(robot)


def test_yasadr_code_rejects_non_string_input():
    with pytest.raises(TypeError, match="robot must be a string"):
        yasadr_code(None)


def test_render_robot_qr_returns_bounded_black_and_white_png():
    payload = render_robot_qr("а1460")

    with Image.open(io.BytesIO(payload)) as image:
        assert image.format == "PNG"
        assert image.width == image.height
        assert 200 <= image.width <= 500
        colors = {
            color for _count, color in image.convert("RGB").getcolors(maxcolors=3)
        }
    assert colors == {(0, 0, 0), (255, 255, 255)}
    assert len(payload) < 100_000
