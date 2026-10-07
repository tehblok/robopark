from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/bot"))

from native.task_cards import format_issues


def test_open_cards_match_legacy_layout_sorting_cleanup_and_escaping():
    issues = [
        {
            "summary": "[a1460] Later normal",
            "priority": {"key": "normal"},
            "statusStartTime": "2026-10-02T10:00:00Z",
            "description": "**Comment:** normal task",
        },
        {
            "summary": "[A999] Camera <left>",
            "priority": {"key": "blocker"},
            "statusStartTime": "2026-10-03T10:00:00Z",
            "description": (
                "**Links:** https://example.test\n"
                "**Comment:** Replace <camera> & inspect " + "x" * 180
            ),
        },
        {
            "summary": "[a1460] Earlier blocker",
            "priority": {"key": "blocker"},
            "statusStartTime": "2026-10-01T10:00:00Z",
            "description": "Plain fallback",
        },
    ]

    text = format_issues("a1460", issues)

    assert text.startswith("Робот <b>a1460</b>, открытые задачи: 3\n\n")
    assert (
        text.index("Earlier blocker")
        < text.index("Camera")
        < text.index("Later normal")
    )
    assert "[a1460]" not in text and "[A999]" not in text
    assert "🔴 <b>Earlier blocker</b>\n   Plain fallback" in text
    assert "🔴 <b>Camera &lt;left&gt;</b>" in text
    assert "Replace &lt;camera&gt; &amp; inspect" in text
    assert "https://example.test" not in text
    assert "…" in text


def test_history_uses_newest_ten_and_closed_date():
    issues = [
        {
            "summary": f"[a1460] repair {day}",
            "priority": {"key": "critical"},
            "description": "done",
            "resolvedAt": f"2026-09-{day:02d}T12:00:00Z",
        }
        for day in range(1, 13)
    ]

    text = format_issues("a1460", issues, history=True)

    assert text.startswith("Робот <b>a1460</b>, история ремонтов: 10")
    assert text.index("repair 12") < text.index("repair 3")
    assert "repair 2" not in text
    assert "<i>закрыт 12.09.2026</i>" in text


def test_active_moves_render_wiki_rows_dates_and_valid_optional_park_ticket():
    issue = {
        "summary": "Перевозка <a1460>",
        "description": (
            "#|\n"
            "||Откуда|Next <b>garage</b>||\n"
            "||Желаемая дата|2026-10-07 15:30:00 +0000||\n"
            "|#"
        ),
        "park_ticket": "PARK-42",
    }

    assert format_issues("a1460", [issue], view="moves") == (
        "Робот <b>a1460</b>, активные задачи транспортировки\n\n"
        "🚚 <b>Перевозка &lt;a1460&gt;</b>\n\n"
        "<b>Откуда:</b> Next garage\n"
        "<b>Желаемая дата и время:</b> 07.10.2026, 18:30 MSK\n\n"
        'Тикет парка: <a href="https://st.yandex-team.ru/PARK-42">PARK-42</a>'
    )
    unsafe = dict(issue, park_ticket='BAD" onclick="alert(1)')
    unsafe_text = format_issues("a1460", [unsafe], view="moves")
    assert "Тикет парка" not in unsafe_text
    assert "onclick" not in unsafe_text


def test_move_history_marks_delivery_and_removes_legacy_title_noise():
    issues = [
        {
            "summary": "Перевозка a1460 Next, Moscow",
            "resolution": {"key": "delivered"},
            "resolvedAt": "2026-10-07T12:00:00Z",
        },
        {
            "summary": "Перевозка a1460 Юг, Moscow",
            "resolution": {"key": "cancelled"},
            "resolvedAt": "2026-10-06T12:00:00Z",
        },
    ]

    text = format_issues("a1460", issues, view="moves_history")

    assert text == (
        "Робот <b>a1460</b>, история последних перемещений: 2\n\n"
        "🚚 <b>Next</b> 🟢\n   <i>закрыт 07.10.2026</i>\n\n"
        "🚚 <b>Юг</b> 🟡\n   <i>закрыт 06.10.2026</i>"
    )


def test_parts_are_sorted_and_grouped_by_component():
    issues = [
        {"key": "SDCWH-2", "description": "Motor\nsecond"},
        {"key": "SDCWH-1", "description": "Camera\nfirst"},
        {"key": "SDCWH-3", "description": "Camera\nthird"},
    ]

    text = format_issues("a1460", issues, view="parts")

    assert text.startswith("Робот <b>a1460</b> · ожидание ЗИП: 3")
    assert text.index("▸ <b>Camera</b> (2)") < text.index("▸ <b>Motor</b> (1)")
    assert text.index("SDCWH-1") < text.index("SDCWH-3")
    assert '<a href="https://st.yandex-team.ru/SDCWH-1">SDCWH-1</a>' in text


def test_every_view_is_bounded_without_cutting_html_tags():
    issues = [
        {
            "key": f"SDCWH-{index + 1}",
            "summary": f"[a1460] <task {index}> " + "T" * 5000,
            "description": "**Comment:** <unsafe> & " + "D" * 5000,
            "priority": {"key": "blocker"},
            "statusStartTime": f"2026-10-{(index % 28) + 1:02d}T10:00:00Z",
            "resolvedAt": f"2026-09-{(index % 28) + 1:02d}T10:00:00Z",
            "resolution": {"key": "delivered"},
        }
        for index in range(40)
    ]

    for view in ("open", "history", "moves", "moves_history", "parts"):
        text = format_issues("<a1460>", issues, view=view, truncated=True)
        assert len(text) <= 3800
        assert text.count("<b>") == text.count("</b>")
        assert text.count("<i>") == text.count("</i>")
        assert text.count("<a ") == text.count("</a>")
        assert "<unsafe>" not in text
        assert "обрезан" in text
