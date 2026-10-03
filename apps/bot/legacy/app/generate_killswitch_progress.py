#!/usr/bin/env python3
"""PNG donut-диаграммы прогресса кампании kill switch по паркам."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from killswitch_logic import aggregate_stats, identify_park_key
from paths import DATA_DIR
from tracker_api import search_issues

OUTPUT_DIR = DATA_DIR / "killswitch_progress"

SUMMARY_QUERY = (
    'Queue: SDCFLEETOPS AND Summary: '
    '"Модернизация роботов кожухом, резинками, kill switch, скобой"'
)

PARKS = [
    ("Next", "Next"),
    ("Север (Москва)", "Север"),
    ("Континент", "Континент"),
    ("Сигма", "Сигма"),
]

# Имена локаций как в TELEGRAM_CHATS / cleaner.py
PROGRESS_LOCATIONS = [park for park, _short in PARKS]

COLOR_DONE = "#22c55e"
COLOR_REMAIN = "#dbeafe"  # мягкий голубой — лучше контраст с зелёным, чем серый
COLOR_TEXT = "#111827"
COLOR_MUTED = "#6b7280"

# 1080×1080 px — хорошо смотрится в Telegram на телефоне и десктопе
FIG_SIZE_IN = 9.0
FIG_DPI = 120

# Диаграмма — максимально крупно
CHART_AXES = [0.01, 0.05, 0.98, 0.82]

CHART_GAP_PX = 1.5        # подзаголовок и футер ↔ диаграмма
TITLE_SUBTITLE_GAP_PX = 1  # KillSwitch ↔ Сервисная кампания

TITLE_FONT = 48
SUBTITLE_FONT = 24
FOOTER_FONT = 28
PCT_FONT = 84
PCT_FONT_100 = 72
DONE_FONT = 24


def _px_to_frac(px: float) -> float:
    return px / (FIG_SIZE_IN * FIG_DPI)


def _place_labels(fig, title, subtitle, footer) -> None:
    """Точные зазоры в px; диаграмма не двигается."""
    chart_top = CHART_AXES[1] + CHART_AXES[3]
    chart_bottom = CHART_AXES[1]
    chart_gap = _px_to_frac(CHART_GAP_PX)
    title_gap = _px_to_frac(TITLE_SUBTITLE_GAP_PX)

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    sub_h = _text_top(fig, subtitle, renderer) - _text_bottom(fig, subtitle, renderer)
    subtitle.set_y(chart_top + chart_gap + sub_h)

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()

    sub_top = _text_top(fig, subtitle, renderer)
    title_h = _text_top(fig, title, renderer) - _text_bottom(fig, title, renderer)
    title.set_y(sub_top + title_gap + title_h)

    footer.set_y(chart_bottom - chart_gap)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    footer.set_y(
        footer.get_position()[1]
        + (chart_bottom - chart_gap) - _text_top(fig, footer, renderer)
    )


def _text_bottom(fig, text_obj, renderer) -> float:
    bbox = text_obj.get_window_extent(renderer)
    fig_bbox = fig.get_window_extent(renderer)
    return (bbox.y0 - fig_bbox.y0) / fig_bbox.height


def _text_top(fig, text_obj, renderer) -> float:
    bbox = text_obj.get_window_extent(renderer)
    fig_bbox = fig.get_window_extent(renderer)
    return (bbox.y1 - fig_bbox.y0) / fig_bbox.height


def fetch_issues() -> list[dict]:
    issues, err = search_issues(SUMMARY_QUERY, order="+created", max_pages=40)
    if err:
        raise RuntimeError(err)
    return issues


def _default_location_rows() -> list[dict]:
    try:
        from store.locations import load_locations

        return load_locations()
    except Exception:
        return [
            {"key": "Next", "tracker_tag": "Next", "display_name": "Next"},
            {"key": "Север (Москва)", "tracker_tag": "МскСевер", "display_name": "Север"},
            {"key": "Континент", "tracker_tag": "Континент", "display_name": "Континент"},
            {"key": "Сигма", "tracker_tag": "Сигма", "display_name": "Сигма"},
        ]


def identify_park(port: str | None, tags: list[str] | None) -> str | None:
    return identify_park_key(port, tags, _default_location_rows())


def aggregate(issues: list[dict]) -> dict[str, dict[str, int]]:
    keys = [park for park, _short in PARKS]
    return aggregate_stats(issues, keys, _default_location_rows())


def display_pct(closed: int, total: int) -> int:
    """Процент без дробной части, округление вверх."""
    if total <= 0 or closed <= 0:
        return 0
    if closed >= total:
        return 100
    return math.ceil(100.0 * closed / total)


def render_donut(
    park_title: str,
    closed: int,
    total: int,
    out_path: Path,
    *,
    title_text: str = "KillSwitch",
    subtitle_text: str = "Сервисная кампания",
) -> None:
    pct = display_pct(closed, total)
    open_count = total - closed
    pct_label = f"{pct}%"
    pct_fontsize = PCT_FONT_100 if pct >= 100 else PCT_FONT

    fig = plt.figure(figsize=(FIG_SIZE_IN, FIG_SIZE_IN), facecolor="white")

    if total == 0:
        fig.text(0.5, 0.5, "Нет данных", ha="center", va="center", fontsize=24, color=COLOR_MUTED)
        fig.savefig(out_path, dpi=FIG_DPI, facecolor="white", pad_inches=0.02)
        plt.close(fig)
        return

    title = fig.text(
        0.5,
        0.9,
        title_text,
        ha="center",
        va="top",
        fontsize=TITLE_FONT,
        fontweight="bold",
        color=COLOR_TEXT,
    )
    subtitle = fig.text(
        0.5,
        0.8,
        subtitle_text,
        ha="center",
        va="top",
        fontsize=SUBTITLE_FONT,
        color=COLOR_MUTED,
    )
    footer = fig.text(
        0.5,
        0.305,
        f"{closed} из {total} роботов",
        ha="center",
        va="top",
        fontsize=FOOTER_FONT,
        color=COLOR_TEXT,
    )

    _place_labels(fig, title, subtitle, footer)

    ax = fig.add_axes(CHART_AXES)
    ax.set_facecolor("white")

    if open_count == 0:
        pie_sizes = [1]
        pie_colors = [COLOR_DONE]
    elif closed == 0:
        pie_sizes = [1]
        pie_colors = [COLOR_REMAIN]
    else:
        pie_sizes = [closed, open_count]
        pie_colors = [COLOR_DONE, COLOR_REMAIN]

    wedges, _ = ax.pie(
        pie_sizes,
        colors=pie_colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.39, "edgecolor": "white", "linewidth": 3},
    )
    if wedges and pie_colors[0] == COLOR_DONE:
        wedges[0].set_edgecolor("#ffffff")

    ax.text(
        0,
        0.03,
        pct_label,
        ha="center",
        va="center",
        fontsize=pct_fontsize,
        fontweight="bold",
        color=COLOR_TEXT,
    )
    ax.text(
        0,
        -0.19,
        "выполнено",
        ha="center",
        va="center",
        fontsize=DONE_FONT,
        color=COLOR_MUTED,
    )
    ax.set_aspect("equal")
    ax.axis("off")

    fig.savefig(out_path, dpi=FIG_DPI, facecolor="white", pad_inches=0.02)
    plt.close(fig)


def progress_png_path(location: str, output_dir: Path = OUTPUT_DIR) -> Path:
    safe = location.replace("/", "-")
    return output_dir / f"{safe}.png"


def generate_all_progress(
    output_dir: Path = OUTPUT_DIR,
    issues: list[dict] | None = None,
    locations: list[str] | None = None,
    *,
    title: str = "KillSwitch",
    subtitle: str = "Сервисная кампания",
    location_rows: list[dict] | None = None,
) -> dict[str, Path]:
    """Сгенерировать PNG по паркам кампании. Возвращает {локация: путь}."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = locations if locations is not None else PROGRESS_LOCATIONS
    rows = location_rows if location_rows is not None else _default_location_rows()
    if issues is None:
        issues = fetch_issues()
    stats = aggregate_stats(issues, list(target), rows)
    paths: dict[str, Path] = {}
    for location in target:
        if location not in stats:
            continue
        closed = stats[location]["closed"]
        total = closed + stats[location]["open"]
        out_path = progress_png_path(location, output_dir)
        render_donut(
            location,
            closed,
            total,
            out_path,
            title_text=title,
            subtitle_text=subtitle,
        )
        paths[location] = out_path
    plt.close("all")
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Donut PNG прогресса kill switch по паркам")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    print("Загрузка задач из Tracker…")
    issues = fetch_issues()
    stats = aggregate(issues)
    print(f"Найдено задач: {len(issues)}")

    paths = generate_all_progress(args.output_dir, issues=issues)
    for location in PROGRESS_LOCATIONS:
        closed = stats[location]["closed"]
        total = closed + stats[location]["open"]
        pct = display_pct(closed, total)
        print(f"  {location:16} {closed:3}/{total:3} ({pct:3}%) → {paths[location].name}")

    print(f"\nГотово: {args.output_dir}")


if __name__ == "__main__":
    main()
