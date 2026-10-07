"""Legacy-compatible in-memory PNG for kill-switch campaigns."""

from __future__ import annotations

import io
import math

COLOR_DONE = "#22c55e"
COLOR_REMAIN = "#dbeafe"
COLOR_TEXT = "#111827"
COLOR_MUTED = "#6b7280"
MAX_CAMPAIGN_ISSUES = 500


def _font(size, *, bold=False):
    from PIL import ImageFont

    names = (
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        )
        if bold
        else (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        )
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _centered(draw, xy, text, *, font, fill):
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    draw.text((xy[0] - width / 2, xy[1]), text, font=font, fill=fill)


def display_pct(closed, total):
    """Match the legacy renderer: integer percentage rounded upward."""
    if total <= 0 or closed <= 0:
        return 0
    if closed >= total:
        return 100
    return math.ceil(100.0 * closed / total)


def campaign_stats(issues, *, truncated=False):
    """Count a bounded visible sample; only exact ``status.key=closed`` is done."""
    rows = list(issues[:MAX_CAMPAIGN_ISSUES])
    closed = sum(
        1
        for issue in rows
        if isinstance(issue.get("status"), dict)
        and issue["status"].get("key") == "closed"
    )
    total = len(rows)
    return {
        "closed": closed,
        "total": total,
        "percentage": display_pct(closed, total),
        "partial": bool(truncated or len(issues) > MAX_CAMPAIGN_ISSUES),
    }


def render_campaign(job, park, issues, truncated=False):
    """Render the old 1080px donut without filesystem or matplotlib state."""
    from PIL import Image, ImageDraw

    stats = campaign_stats(issues, truncated=truncated)
    closed = stats["closed"]
    total = stats["total"]
    percentage = stats["percentage"]
    partial = stats["partial"]

    image = Image.new("RGB", (1080, 1080), "white")
    draw = ImageDraw.Draw(image)
    title = str(job.get("tracker_tag") or job.get("title") or "KillSwitch").strip()
    park_name = str(park.get("name") or "").strip()

    if total == 0:
        message = "Нет данных в видимой выборке" if partial else "Нет данных"
        _centered(
            draw,
            (540, 500),
            message,
            font=_font(32),
            fill=COLOR_MUTED,
        )
        if partial:
            _centered(
                draw,
                (540, 555),
                "Данные ограничены",
                font=_font(24),
                fill=COLOR_MUTED,
            )
    else:
        _centered(draw, (540, 5), title, font=_font(80, bold=True), fill=COLOR_TEXT)
        _centered(
            draw,
            (540, 100),
            "Сервисная кампания",
            font=_font(40),
            fill=COLOR_MUTED,
        )

        outer_box = (120, 150, 960, 990)
        inner_box = (285, 315, 795, 825)
        if closed <= 0:
            draw.ellipse(outer_box, fill=COLOR_REMAIN)
        elif closed >= total:
            draw.ellipse(outer_box, fill=COLOR_DONE)
        else:
            done_angle = 360.0 * closed / total
            draw.ellipse(outer_box, fill=COLOR_REMAIN)
            draw.pieslice(
                outer_box,
                start=-90,
                end=-90 + done_angle,
                fill=COLOR_DONE,
            )
            # The legacy chart separates both slices with a thin white edge.
            for angle in (-90, -90 + done_angle):
                radians = math.radians(angle)
                draw.line(
                    (
                        540 + 255 * math.cos(radians),
                        570 + 255 * math.sin(radians),
                        540 + 420 * math.cos(radians),
                        570 + 420 * math.sin(radians),
                    ),
                    fill="white",
                    width=3,
                )
        draw.ellipse(inner_box, fill="white")

        pct_text = f"{percentage}%*" if partial else f"{percentage}%"
        pct_size = 120 if percentage >= 100 else 140
        _centered(
            draw,
            (540, 450),
            pct_text,
            font=_font(pct_size, bold=True),
            fill=COLOR_TEXT,
        )
        done_text = "выполнено среди видимых" if partial else "выполнено"
        _centered(
            draw,
            (540, 625),
            done_text,
            font=_font(40),
            fill=COLOR_MUTED,
        )

        footer = f"{closed} из {total}"
        footer += " видимых роботов" if partial else " роботов"
        if park_name:
            footer += f" · {park_name}"
        _centered(
            draw,
            (540, 1015),
            footer,
            font=_font(47),
            fill=COLOR_TEXT,
        )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
