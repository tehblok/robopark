"""Default Zoom reminder job payloads (seeded into data/schedules.json).

One park → one job: own fire_at, text, link. Morning A/B via ``alternate``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

KIND_PLANNER_AB = "planner_ab"  # legacy multi-group; prefer simple + alternate
KIND_SIMPLE = "simple"

DAILY_TEXT = (
    "🔔 10 минут до утренней встречи. Небольшая пауза в делах — время на связь.\n\n"
    "Zoom здесь:\n{link}"
)

WEEKLY_TEXT = (
    "🔔 10 минут до встречи. Время собраться с мыслями и подключиться.\n\n"
    "Zoom здесь:\n{link}"
)

# weekdays: 0=Mon … 6=Sun; None = every day
# Lunch Zoom slots: Wed–Fri (formerly Thursday-only).
WEEKLY_DAYS = [2, 3, 4]

# Old combined slots — removed on load/migrate in favour of per-park jobs.
LEGACY_COMBINED_IDS = frozenset(
    {
        "daily_slot_1",
        "daily_slot_2",
        "daily_slot_3",
        "daily_slot_4",
        "kazan_nn_weekly",
    }
)

WEEKLY_LEGACY_IDS = frozenset(
    {
        "next_weekly",
        "yug_weekly",
        "kazan_weekly",
        "nn_weekly",
        "rokalab_weekly",
        "kazan_nn_weekly",  # old shared
    }
)


def _morning(
    job_id: str,
    *,
    label: str,
    location: str,
    fire_at: str,
    link: str,
    alternate: str,
) -> dict[str, Any]:
    return {
        "id": job_id,
        "label": label,
        "enabled": False,
        "fire_at": fire_at,
        "weekdays": None,
        "kind": KIND_SIMPLE,
        "alternate": alternate,  # "A" | "B" | None — через день от planner_anchor
        "text": DAILY_TEXT,
        "locations": [location],
        "link": link,
    }


def _weekly(
    job_id: str,
    *,
    label: str,
    location: str,
    fire_at: str,
    link: str,
) -> dict[str, Any]:
    return {
        "id": job_id,
        "label": label,
        "enabled": False,
        "fire_at": fire_at,
        "weekdays": list(WEEKLY_DAYS),
        "kind": KIND_SIMPLE,
        "alternate": None,
        "text": WEEKLY_TEXT,
        "locations": [location],
        "link": link,
    }


# Morning: same wall-clock as former shared A/B slots; each park editable alone.
DEFAULT_ZOOM_JOBS: list[dict[str, Any]] = [
    _morning(
        "morning_argatkaz",
        label="АРГАТЕХКАЗ (утро A)",
        location="АРГАТЕХКАЗ",
        fire_at="10:10",
        link="",
        alternate="A",
    ),
    _morning(
        "morning_argatnn",
        label="АРГАТЕХНН (утро A)",
        location="АРГАТЕХНН",
        fire_at="10:10",
        link="",
        alternate="A",
    ),
    _morning(
        "morning_continent",
        label="Континент (утро B)",
        location="Континент",
        fire_at="10:10",
        link="",
        alternate="B",
    ),
    _morning(
        "morning_yug",
        label="Юг (утро A)",
        location="Юг",
        fire_at="09:25",
        link="",
        alternate="A",
    ),
    _morning(
        "morning_sever",
        label="Север (утро B)",
        location="Север (Москва)",
        fire_at="09:25",
        link="",
        alternate="B",
    ),
    _morning(
        "morning_sigma",
        label="Сигма (утро A)",
        location="Сигма",
        fire_at="09:40",
        link="",
        alternate="A",
    ),
    _morning(
        "morning_astana",
        label="Астана (утро B)",
        location="КалиевАстана",
        fire_at="09:40",
        link="",
        alternate="B",
    ),
    _morning(
        "morning_almaty",
        label="Алматы (утро B)",
        location="КалиевАлматы",
        fire_at="09:40",
        link="",
        alternate="B",
    ),
    _morning(
        "morning_next",
        label="Next (утро A)",
        location="Next",
        fire_at="09:55",
        link="",
        alternate="A",
    ),
    _morning(
        "morning_rokalab",
        label="РОКАЛАБ (утро B)",
        location="РОКАЛАБ",
        fire_at="09:55",
        link="",
        alternate="B",
    ),
    _weekly(
        "next_weekly",
        label="Next (ср–пт)",
        location="Next",
        fire_at="09:50",
        link="",
    ),
    _weekly(
        "yug_weekly",
        label="Юг (ср–пт)",
        location="Юг",
        fire_at="09:50",
        link="",
    ),
    _weekly(
        "kazan_weekly",
        label="АРГАТЕХКАЗ (ср–пт)",
        location="АРГАТЕХКАЗ",
        fire_at="09:50",
        link="",
    ),
    _weekly(
        "nn_weekly",
        label="АРГАТЕХНН (ср–пт)",
        location="АРГАТЕХНН",
        fire_at="09:50",
        link="",
    ),
    _weekly(
        "rokalab_weekly",
        label="Рокалаб (ср–пт)",
        location="РОКАЛАБ",
        fire_at="09:50",
        link="",
    ),
]

_EDITABLE_JOB_KEYS = (
    "enabled",
    "fire_at",
    "weekdays",
    "label",
    "kind",
    "text",
    "link",
    "locations",
    "groups",
    "alternate",
)


def default_zoom_jobs() -> list[dict[str, Any]]:
    return deepcopy(DEFAULT_ZOOM_JOBS)


def parks_summary(job: dict[str, Any]) -> str:
    """Short park label for admin list."""
    kind = job.get("kind") or KIND_SIMPLE
    alt = job.get("alternate")
    alt_s = f" · {alt}" if alt in ("A", "B") else ""
    if kind == KIND_PLANNER_AB:
        groups = job.get("groups") or {}
        a = (groups.get("A") or {}).get("label") or ",".join(
            (groups.get("A") or {}).get("locations") or []
        )
        b = (groups.get("B") or {}).get("label") or ",".join(
            (groups.get("B") or {}).get("locations") or []
        )
        a = str(a or "?").strip()
        b = str(b or "?").strip()
        if a and b:
            return f"{a} / {b}"
        return (a or b or "?") + alt_s
    locs = job.get("locations") or []
    if not locs:
        return "?" + alt_s
    if len(locs) == 1:
        return str(locs[0]) + alt_s
    return f"{locs[0]}+{len(locs) - 1}{alt_s}"
