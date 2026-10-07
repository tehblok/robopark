from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

BOT = Path(__file__).resolve().parents[2] / "apps/bot"
sys.path.insert(0, str(BOT))

from native.campaigns import campaign_stats, display_pct, render_campaign


def issue(status):
    return {"status": status}


def test_progress_rounds_up_and_only_exact_closed_status_is_done():
    issues = [
        issue({"key": "closed"}),
        issue({"key": "Closed"}),
        issue("closed"),
        issue({"key": "inProgress"}),
        issue(None),
        issue({"key": "open"}),
    ]

    assert campaign_stats(issues) == {
        "closed": 1,
        "total": 6,
        "percentage": 17,
        "partial": False,
    }
    assert display_pct(1, 3) == 34
    assert display_pct(3, 3) == 100


def test_progress_caps_input_and_marks_partial_visible_sample():
    issues = [issue({"key": "closed"}) for _ in range(501)]
    assert campaign_stats(issues) == {
        "closed": 500,
        "total": 500,
        "percentage": 100,
        "partial": True,
    }
    assert campaign_stats([issue({"key": "closed"})], truncated=True)["partial"] is True


def test_campaign_png_is_legacy_size_and_palette():
    png = render_campaign(
        {"title": "SK fallback", "tracker_tag": "KillSwitch"},
        {"name": "Next"},
        [issue({"key": "closed"}), issue({"key": "open"})],
    )

    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert image.size == (1080, 1080)
        colors = set(image.get_flattened_data())
    assert (34, 197, 94) in colors
    assert (219, 234, 254) in colors
    assert len(png) < 9 * 1024 * 1024


def test_empty_and_truncated_all_done_images_remain_valid():
    empty = render_campaign({"title": "SK"}, {"name": "Next"}, [])
    partial = render_campaign(
        {"title": "SK"},
        {"name": "Next"},
        [issue({"key": "closed"})],
        truncated=True,
    )

    for payload in (empty, partial):
        with Image.open(io.BytesIO(payload)) as image:
            assert image.size == (1080, 1080)
    full = render_campaign(
        {"title": "SK"},
        {"name": "Next"},
        [issue({"key": "closed"})],
    )
    assert partial != full
