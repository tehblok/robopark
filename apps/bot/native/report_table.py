"""Report table layout retained from the fixed tracker-report-server renderer.

Only rendering lives here. Input data and permissions belong to Robopark.
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

CATEGORY_STYLES = {
    "< 24 ч":  {"bg": "#34d399", "fg": "#064e3b"},
    "24–48 ч": {"bg": "#fbbf24", "fg": "#78350f"},
    "48–72 ч": {"bg": "#fb923c", "fg": "#431407"},
    "> 72 ч":  {"bg": "#f87171", "fg": "#450a0a"},
}
CATEGORY_ORDER = list(CATEGORY_STYLES.keys())

REPAIR_STYLES = {
    "< 3 ч":  {"bg": "#34d399", "fg": "#064e3b"},
    "3–5 ч": {"bg": "#fbbf24", "fg": "#78350f"},
    "> 5 ч":  {"bg": "#f87171", "fg": "#450a0a"},
    "Слив логов": {"bg": "#93c5fd", "fg": "#1e3a8a"},
}
REPAIR_ORDER = list(REPAIR_STYLES.keys())

TITLE_FONT_SIZE = 30
TITLE_TOP_IN = 0.55  # отступ от верха изображения до верха заголовка
TITLE_LINE_IN = TITLE_FONT_SIZE / 72.0 * 1.35  # реальная высота bold-шрифта
TITLE_GAP_IN = TITLE_FONT_SIZE / 72.0 / 2  # зазор до шапки = ½× размер шрифта
TITLE_BAND_IN = TITLE_TOP_IN + TITLE_LINE_IN + TITLE_GAP_IN

LEGEND_BOX_W_IN = 0.92
LEGEND_BOX_H_IN = 0.24
LEGEND_GAP_IN = 0.07
LEGEND_ROW_GAP_IN = 0.12  # между строками легенды (было 0.08)
LEGEND_BOTTOM_IN = 0.12
LEGEND_TABLE_GAP_IN = TITLE_GAP_IN  # фикс. зазор таблица → легенда
LEGEND_BLOCK_IN = LEGEND_BOTTOM_IN + LEGEND_BOX_H_IN * 2 + LEGEND_ROW_GAP_IN
LEGEND_AREA_IN = LEGEND_TABLE_GAP_IN + LEGEND_BLOCK_IN
LEGEND_FONT_SIZE = 10
LEGEND_LABEL_FONT_SIZE = LEGEND_FONT_SIZE + 2
LEGEND_LABEL_GAP_IN = 0.12  # зазор подпись → первый кубик

TABLE_SCALE_Y = 1.6
TABLE_AREA_H = 0.92
TARGET_HEADER_IN = 0.56  # фикс. высота шапки в дюймах (2× от 0.28)
REFERENCE_ROW_COUNT = 24  # эталон — текущий Next
FIG_BODY_BASE_IN = 1.8
HEADER_FONT_SIZE = 14


def _layout_constants() -> tuple[float, float, float]:
    """Высота строки данных и overhead fig — по эталону Next (24 задачи)."""
    fig_h_ref = (
        0.42 * REFERENCE_ROW_COUNT + FIG_BODY_BASE_IN + LEGEND_AREA_IN + TITLE_BAND_IN
    )
    legend_frac = LEGEND_AREA_IN / fig_h_ref
    title_frac = TITLE_BAND_IN / fig_h_ref
    ax_h_ref = fig_h_ref * (1 - legend_frac - title_frac)
    data_frac_ref = TABLE_AREA_H - TARGET_HEADER_IN / ax_h_ref
    target_row_in = (data_frac_ref * ax_h_ref) / REFERENCE_ROW_COUNT
    fig_overhead_in = fig_h_ref - REFERENCE_ROW_COUNT * target_row_in
    return target_row_in, fig_overhead_in, fig_h_ref


TARGET_ROW_IN, FIG_OVERHEAD_IN, _REF_FIG_H = _layout_constants()
SPACER_ROW_IN = TARGET_ROW_IN / 6

HEADER_BG = "#1e293b"
HEADER_FG = "#f8fafc"
ROW_ALT_BG = "#f1f5f9"
ROW_BG = "#ffffff"
BORDER_COLOR = "#cbd5e1"
TITLE_COLOR = "#0f172a"

DISPLAY_COLS = ["Задача", "Статус", "В ремонте", "Часы простоя"]
COLUMN_HEADERS = {
    "Часы простоя": "Часы\nпростоя",
}
# Порядок групп в таблице; неизвестные статусы — в конце.
STATUS_ORDER = [
    "Ожидание поставки",
    "Ждем смежников",
    "Перемещение",
    "В работе",
    "Пауза",
    "В очереди",
]
TASK_MAX_LEN = 42
# Заголовок над PNG (ключ локации в данных → подпись в шапке)
LOCATION_TITLE_LABELS = {
    "КалиевАстана": "Астана",
    "КалиевАлматы": "Алматы",
    "АрмаМСК": "Арма",
}
CELL_PAD = 0.014           # отступ текста от краёв ячейки (данные)
HEADER_CELL_PAD = 0.012    # отступ в шапке
COL_SIDE_PAD_IN = 0.07     # зазор слева + справа при расчёте ширины колонки
TABLE_LEFT = 0.008         # левый край таблицы в axes
DATA_FONT_SIZE = 11


def _header_label(col: str) -> str:
    return COLUMN_HEADERS.get(col, col)


def _header_width(col: str) -> int:
    label = _header_label(col)
    if "\n" in label:
        return max(len(line) for line in label.split("\n"))
    return len(label)


def _truncate_task(text) -> str:
    text = str(text)
    if len(text) <= TASK_MAX_LEN:
        return text
    return text[: TASK_MAX_LEN - 1] + "…"


def _format_age(hours: float) -> str:
    return f"{int(round(hours))} ч"


def _age_category(hours: float) -> str:
    if hours < 24:
        return "< 24 ч"
    if hours < 48:
        return "24–48 ч"
    if hours < 72:
        return "48–72 ч"
    return "> 72 ч"


def _format_repair_hours(hours: float) -> str:
    return f"{int(hours)} ч"


def _repair_category(hours: float) -> str:
    if hours < 3:
        return "< 3 ч"
    if hours <= 5:
        return "3–5 ч"
    return "> 5 ч"


def _hours_from_display(text) -> float:
    return float(str(text).replace(" ч", "").strip() or 0)


def _split_status_groups(table_df: pd.DataFrame) -> list[pd.DataFrame]:
    if table_df.empty:
        return []
    groups: list[pd.DataFrame] = []
    start = 0
    statuses = table_df["Статус"].tolist()
    for idx in range(1, len(statuses)):
        if statuses[idx] != statuses[idx - 1]:
            groups.append(table_df.iloc[start:idx].reset_index(drop=True))
            start = idx
    groups.append(table_df.iloc[start:].reset_index(drop=True))
    return groups


def _data_area_height(table_df: pd.DataFrame) -> float:
    groups = _split_status_groups(table_df)
    spacer_count = max(0, len(groups) - 1)
    return len(table_df) * TARGET_ROW_IN + spacer_count * SPACER_ROW_IN


def _text_width_in(
    fig, text: str, fontsize: float, *, fontweight: str = "normal",
) -> float:
    return _text_width_frac(fig, text, fontsize, fontweight=fontweight) * fig.get_size_inches()[0]


def _header_width_in(fig, col: str) -> float:
    label = _header_label(col)
    if "\n" in label:
        return max(
            _text_width_in(fig, line, HEADER_FONT_SIZE, fontweight="bold")
            for line in label.split("\n")
        )
    return _text_width_in(fig, label, HEADER_FONT_SIZE, fontweight="bold")


def _compute_col_widths(columns, table_df, fig) -> tuple[list[float], float]:
    """Ширина колонок по тексту + симметричные отступы слева и справа."""
    side_pad = COL_SIDE_PAD_IN * 2
    widths_in: list[float] = []
    for col in columns:
        if col == "Задача":
            w = max((_text_width_in(fig, str(text), DATA_FONT_SIZE) for text in table_df[col]), default=0) + side_pad
            w = max(w, _header_width_in(fig, col) + side_pad)
        else:
            texts = table_df[col].astype(str)
            text_width = max((_text_width_in(fig, text, DATA_FONT_SIZE) for text in texts), default=0)
            w = max(
                text_width,
                _header_width_in(fig, col),
            ) + side_pad
        widths_in.append(w)

    total_in = sum(widths_in)
    return [w / total_in for w in widths_in], total_in


def _table_height_in(table_df: pd.DataFrame) -> float:
    return TARGET_HEADER_IN + _data_area_height(table_df)


def _figure_height(table_df: pd.DataFrame) -> float:
    return max(3.2, TITLE_BAND_IN + _table_height_in(table_df) + LEGEND_AREA_IN)


def _header_bbox(table_df: pd.DataFrame) -> list[float]:
    ax_h_in = _table_height_in(table_df)
    header_frac = TARGET_HEADER_IN / ax_h_in
    return [TABLE_LEFT, 1 - header_frac, 1 - 2 * TABLE_LEFT, header_frac]


def _render_data_groups(
    ax,
    table_df: pd.DataFrame,
    col_widths: list[float],
    repair_hours: list[float],
) -> None:
    groups = _split_status_groups(table_df)
    ax_h_in = _table_height_in(table_df)
    header_frac = TARGET_HEADER_IN / ax_h_in
    spacer_frac = SPACER_ROW_IN / ax_h_in
    y = 1 - header_frac  # верх данных — сразу под шапкой
    row_offset = 0

    for group_idx, group_df in enumerate(groups):
        group_rows = len(group_df)
        group_frac = (group_rows * TARGET_ROW_IN) / ax_h_in
        y -= group_frac
        data_tbl = ax.table(
            cellText=group_df.values,
            colWidths=col_widths,
            bbox=[TABLE_LEFT, y, 1 - 2 * TABLE_LEFT, group_frac],
            cellLoc="center",
        )
        data_tbl.auto_set_font_size(False)
        data_tbl.set_fontsize(11)
        data_tbl.scale(1, TABLE_SCALE_Y)
        _style_data(
            data_tbl, group_df, group_rows, row_offset=row_offset,
            repair_hours=repair_hours[row_offset:row_offset + group_rows],
        )
        row_offset += group_rows
        if group_idx < len(groups) - 1:
            y -= spacer_frac


def _style_header(tbl, columns) -> None:
    for (_, col_idx), cell in tbl.get_celld().items():
        cell.set_edgecolor(BORDER_COLOR)
        cell.set_linewidth(0.6)

    for col_idx, col_name in enumerate(columns):
        cell = tbl[(0, col_idx)]
        cell.set_facecolor(HEADER_BG)
        cell.get_text().set_text(_header_label(col_name))
        cell.set_text_props(
            color=HEADER_FG,
            fontweight="bold",
            ha="center",
            va="center",
            fontsize=HEADER_FONT_SIZE,
        )
        cell.PAD = HEADER_CELL_PAD


def _style_data(
    tbl,
    table_df,
    row_count,
    row_offset: int = 0,
    repair_hours: list[float] | None = None,
) -> None:
    repair_idx = list(table_df.columns).index("В ремонте")
    age_idx = list(table_df.columns).index("Часы простоя")
    task_idx = list(table_df.columns).index("Задача")
    colored_cols = {repair_idx, age_idx}
    col_count = len(table_df.columns)

    for (_, col_idx), cell in tbl.get_celld().items():
        cell.set_edgecolor(BORDER_COLOR)
        cell.set_linewidth(0.6)

    for row_idx, repair_text in enumerate(table_df["В ремонте"]):
        if str(repair_text) == "Слив логов":
            style = REPAIR_STYLES["Слив логов"]
        elif str(repair_text) == "—":
            style = {"bg": "#e5e7eb", "fg": "#475569"}
        elif repair_hours is not None:
            hours = repair_hours[row_idx]
            style = REPAIR_STYLES.get(_repair_category(hours), {"bg": "#ffffff", "fg": "#0f172a"})
        else:
            hours = _hours_from_display(repair_text)
            style = REPAIR_STYLES.get(_repair_category(hours), {"bg": "#ffffff", "fg": "#0f172a"})
        cell = tbl[(row_idx, repair_idx)]
        cell.set_facecolor(style["bg"])
        cell.set_text_props(
            ha="center",
            va="center",
            fontweight="bold",
            color=style["fg"],
        )
        cell.PAD = CELL_PAD

    for row_idx, age_text in enumerate(table_df["Часы простоя"]):
        if str(age_text) == "—":
            style = {"bg": "#e5e7eb", "fg": "#475569"}
        else:
            hours = _hours_from_display(age_text)
            style = CATEGORY_STYLES.get(_age_category(hours), {"bg": "#ffffff", "fg": "#0f172a"})
        cell = tbl[(row_idx, age_idx)]
        cell.set_facecolor(style["bg"])
        cell.set_text_props(
            ha="center",
            va="center",
            fontweight="bold",
            color=style["fg"],
        )
        cell.PAD = CELL_PAD

    for row_idx in range(row_count):
        bg = ROW_BG if (row_offset + row_idx) % 2 == 0 else ROW_ALT_BG
        for col_idx in range(col_count):
            if col_idx in colored_cols:
                continue
            cell = tbl[(row_idx, col_idx)]
            cell.set_facecolor(bg)
            if col_idx == task_idx:
                cell.set_text_props(ha="left", va="center", color="#1e293b", fontsize=11)
            else:
                cell.set_text_props(ha="center", va="center", color="#1e293b", fontsize=11)
            cell.PAD = CELL_PAD


def _text_width_frac(
    fig, text: str, fontsize: float, *, fontweight: str = "normal",
) -> float:
    fw = fig.get_size_inches()[0]
    probe = fig.text(-100, -100, text, fontsize=fontsize, fontweight=fontweight, alpha=0.0)
    fig.canvas.draw()
    w_px = probe.get_window_extent(fig.canvas.get_renderer()).width
    probe.remove()
    return w_px / (fw * fig.dpi)


def _add_legend_row(
    fig,
    row_index: int,
    label: str,
    styles: dict[str, dict[str, str]],
    order: list[str],
    *,
    labels_x: float,
    boxes_x: float,
    box_w: float,
    box_h: float,
    gap: float,
) -> None:
    _, fh = fig.get_size_inches()
    y = (LEGEND_BOTTOM_IN + row_index * (LEGEND_BOX_H_IN + LEGEND_ROW_GAP_IN)) / fh

    fig.text(
        labels_x,
        y + box_h / 2,
        label,
        transform=fig.transFigure,
        ha="right",
        va="center",
        fontsize=LEGEND_LABEL_FONT_SIZE,
        fontweight="bold",
        color=HEADER_BG,
    )

    for i, cat in enumerate(order):
        style = styles[cat]
        x = boxes_x + i * (box_w + gap)
        patch = FancyBboxPatch(
            (x, y),
            box_w,
            box_h,
            boxstyle="round,pad=0.002",
            facecolor=style["bg"],
            edgecolor=BORDER_COLOR,
            linewidth=0.6,
            transform=fig.transFigure,
            clip_on=False,
        )
        fig.add_artist(patch)
        fig.text(
            x + box_w / 2,
            y + box_h / 2,
            cat,
            transform=fig.transFigure,
            ha="center",
            va="center",
            fontsize=LEGEND_FONT_SIZE,
            fontweight="bold",
            color=style["fg"],
        )


def _add_category_legend(fig) -> None:
    """Две легенды под таблицей — подписи и кубики на одной вертикали."""
    rows = [
        ("Часы простоя:", CATEGORY_STYLES, CATEGORY_ORDER),
        ("В ремонте:", REPAIR_STYLES, REPAIR_ORDER),
    ]
    fw, _fh = fig.get_size_inches()
    box_w = LEGEND_BOX_W_IN / fw
    box_h = LEGEND_BOX_H_IN / _fh
    gap = LEGEND_GAP_IN / fw
    label_gap = LEGEND_LABEL_GAP_IN / fw

    max_label_w = max(
        _text_width_frac(fig, label, LEGEND_LABEL_FONT_SIZE, fontweight="bold")
        for label, _, _ in rows
    )
    max_boxes_w = max(
        len(order) * box_w + (len(order) - 1) * gap
        for _, _, order in rows
    )
    block_start = 0.5 - (max_label_w + label_gap + max_boxes_w) / 2
    labels_x = block_start + max_label_w
    boxes_x = block_start + max_label_w + label_gap

    for row_index, (label, styles, order) in enumerate(rows):
        _add_legend_row(
            fig,
            row_index,
            label,
            styles,
            order,
            labels_x=labels_x,
            boxes_x=boxes_x,
            box_w=box_w,
            box_h=box_h,
            gap=gap,
        )


def _location_title_text(location: str, row_count: int) -> str:
    return f"{location}  ·  активных задач: {row_count}"


def _add_location_title(fig, location: str, row_count: int, fig_h: float) -> None:
    title_y = 1 - TITLE_TOP_IN / fig_h
    fig.text(
        0.5,
        title_y,
        _location_title_text(location, row_count),
        transform=fig.transFigure,
        ha="center",
        va="top",
        fontsize=TITLE_FONT_SIZE,
        fontweight="bold",
        color=TITLE_COLOR,
        clip_on=False,
    )


def render_location_png(
    location: str, table_df: pd.DataFrame, repair_hours: list[float | None],
    *, total_count: int | None = None, note: str = "",
) -> bytes:
    if table_df.empty:
        raise ValueError(f"Нет активных задач для локации «{location}»")

    row_count = len(table_df) if total_count is None else total_count
    columns = list(table_df.columns)
    fig_h = _figure_height(table_df) + (0.28 if note else 0)
    table_h = _table_height_in(table_df)

    fig, ax = plt.subplots(figsize=(8, fig_h))
    fig.patch.set_facecolor("#e2e8f0")
    ax.set_facecolor("#ffffff")
    ax.axis("off")

    col_widths, content_w_in = _compute_col_widths(columns, table_df, fig)
    table_area_frac = 0.98 * (1 - 2 * TABLE_LEFT)
    title_w_in = _text_width_in(
        fig,
        _location_title_text(location, row_count),
        TITLE_FONT_SIZE,
        fontweight="bold",
    )
    fig_w = max(5.0, content_w_in / table_area_frac + 0.25, title_w_in + 0.5)
    fig.set_size_inches(fig_w, fig_h)

    legend_frac = LEGEND_AREA_IN / fig_h
    ax.set_position([0.01, legend_frac, 0.98, table_h / fig_h])
    col_widths, _ = _compute_col_widths(columns, table_df, fig)

    _add_location_title(fig, location, row_count, fig_h)

    header_tbl = ax.table(
        cellText=[[_header_label(col) for col in columns]],
        colWidths=col_widths,
        bbox=_header_bbox(table_df),
        cellLoc="center",
    )
    header_tbl.auto_set_font_size(False)
    header_tbl.set_fontsize(11)
    _style_header(header_tbl, columns)
    _render_data_groups(ax, table_df, col_widths, repair_hours)
    _add_category_legend(fig)
    if note:
        fig.text(0.5, 1 - (TITLE_BAND_IN - 0.05) / fig_h, note, ha="center", va="top", fontsize=10, color="#475569")

    buf = io.BytesIO()
    try:
        plt.savefig(
            buf,
            format="png",
            dpi=180,
            facecolor=fig.get_facecolor(),
            edgecolor="none",
            pad_inches=0.22,
        )
        plt.close(fig)
        return buf.getvalue()
    finally:
        plt.close(fig)
        buf.close()
