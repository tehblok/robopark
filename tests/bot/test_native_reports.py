from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

BOT = Path(__file__).resolve().parents[2] / "apps/bot"
sys.path.insert(0, str(BOT))

from native.reports import (
    EMPTY_WATCHDOG_TEXT,
    render_report,
    report_page_count,
    report_rows,
    watchdog_parts,
)


def issue(summary, status, repair, downtime, *, log_dump=False):
    return {
        "key": summary,
        "summary": summary,
        "status": {"key": status},
        "bot_report": {
            "repair_hours": repair,
            "downtime_hours": downtime,
            "log_dump": log_dump,
        },
    }


def test_rows_preserve_legacy_groups_sorting_and_color_thresholds():
    rows = report_rows(
        [
            issue("moving", "moving", 0, 6),
            issue("queued", "queued", 5, 48),
            issue("logs", "queued", None, 3, log_dump=True),
            issue("parts", "delieveryWaiting", 2, 72),
            issue("team", "waitingForAnotherTeam", 3, 24),
            issue("unknown", "pause", None, None),
        ]
    )

    assert [row["task"] for row in rows] == [
        "parts",
        "team",
        "moving",
        "unknown",
        "logs",
        "queued",
    ]
    assert [row["repair_category"] for row in rows[:5]] == [
        "green",
        "yellow",
        "green",
        "unknown",
        "log_dump",
    ]
    assert [row["downtime_category"] for row in rows[:5]] == [
        "red",
        "yellow",
        "green",
        "unknown",
        "green",
    ]


def test_watchdog_uses_exact_empty_phrase_when_no_measured_queue_task():
    assert watchdog_parts([]) == []
    assert watchdog_parts([issue("parts", "delieveryWaiting", 10, 30)]) == [
        EMPTY_WATCHDOG_TEXT
    ]
    unknown = watchdog_parts([issue("unknown", "queued", None, 30)])
    assert unknown == [
        "Время в очереди не рассчитано: 1. Нет подтверждённой истории статусов."
    ]


def test_watchdog_sections_keep_legacy_order_thresholds_and_escape_tracker_text():
    parts = watchdog_parts(
        [
            issue("<robot> logs", "queued", None, 4, log_dump=True),
            issue("red & delayed", "queued", 5.1, 10),
            issue("yellow", "open", 3, 10),
            issue("green", "diagnostics", 2.9, 10),
        ]
    )

    assert [part.split(":", 1)[0] for part in parts] == [
        "<b>Задача по сливу логов",
        "<b>Превышено время в очереди",
        "<b>Время отведенное на ремонт истекает",
        "<b>Новые задачи",
    ]
    assert "&lt;robot&gt; logs" in parts[0]
    assert "red &amp; delayed" in parts[1]
    assert "5ч" in parts[1]
    assert "3ч" in parts[2]
    assert "2ч" in parts[3]
    assert all(len(part) <= 3900 for part in parts)


def test_rendered_png_is_bounded_and_contains_legacy_cell_palette():
    png = render_report(
        {"kind": "report", "title": "Открытые блокеры"},
        {"name": "Next"},
        [
            issue("green", "queued", 2, 20),
            issue("yellow", "queued", 3, 30),
            issue("red", "queued", 6, 50),
            issue("logs", "queued", None, 80, log_dump=True),
        ],
    )

    with Image.open(io.BytesIO(png)) as image:
        assert image.format == "PNG"
        assert 800 <= image.width <= 6000
        assert image.height < 10_000
        colors = set(image.convert("RGB").get_flattened_data())
    assert (52, 211, 153) in colors
    assert (251, 191, 36) in colors
    assert (248, 113, 113) in colors
    assert (147, 197, 253) in colors
    assert (251, 146, 60) in colors
    assert len(png) < 9 * 1024 * 1024


def test_report_pagination_covers_every_row_with_safe_page_dimensions():
    issues = [issue(f"task-{index}", "queued", 1, index) for index in range(81)]
    assert report_page_count(issues) == 3
    pages = [
        render_report(
            {"kind": "report", "title": "Открытые блокеры"},
            {"name": "Next"},
            issues,
            page=page,
        )
        for page in range(3)
    ]
    for payload in pages:
        with Image.open(io.BytesIO(payload)) as image:
            assert image.width + image.height < 10_000


def test_long_cyrillic_task_text_fits_its_column_with_visible_ellipsis(monkeypatch):
    from matplotlib.figure import Figure
    from matplotlib.table import Table

    captured = []
    original = Figure.savefig

    def capture(fig, *args, **kwargs):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for ax in fig.axes:
            for table in ax.get_children():
                if isinstance(table, Table):
                    for cell in table.get_celld().values():
                        text = cell.get_text()
                        if text.get_text().startswith("Ш"):
                            captured.append(text.get_text())
                            bounds = text.get_window_extent(renderer)
                            cell_bounds = cell.get_window_extent(renderer)
                            assert bounds.x0 >= cell_bounds.x0
                            assert bounds.x1 <= cell_bounds.x1
        return original(fig, *args, **kwargs)

    monkeypatch.setattr(Figure, "savefig", capture)
    render_report({}, {"name": "Next"}, [issue("Ш" * 80, "queued", 2, 20)])
    assert captured and captured[0].endswith("…")
