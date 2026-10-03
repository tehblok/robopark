"""Локальные правила задач для диспетчер-бота (по заголовку)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from config import DATA_DIR

OVERRIDES_FILE = Path(DATA_DIR) / "dispatcher_task_overrides.csv"

PRIORITY_RANK = {
    "blocker": 0,
    "critical": 1,
    "normal": 2,
}

_cache: dict[str, TaskRule] | None = None


@dataclass(frozen=True)
class TaskRule:
    description: str = ""
    display_title: str = ""
    min_priority: str | None = None
    hidden: bool = False


def _title_key(title: str) -> str:
    return title.strip().casefold()


def _parse_bool(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"1", "true", "yes", "да", "y"}


def _parse_min_priority(value: str | None) -> str | None:
    key = (value or "").strip().casefold()
    if not key:
        return None
    if key not in PRIORITY_RANK:
        return None
    return key


def load_task_rules() -> dict[str, TaskRule]:
    global _cache
    if _cache is not None:
        return _cache

    rules: dict[str, TaskRule] = {}
    if OVERRIDES_FILE.exists():
        with OVERRIDES_FILE.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                match_title = (row.get("match_title") or "").strip()
                description = (row.get("description") or "").strip()
                display_title = (row.get("display_title") or "").strip()
                if not match_title:
                    continue
                rules[_title_key(match_title)] = TaskRule(
                    description=description,
                    display_title=display_title,
                    min_priority=_parse_min_priority(row.get("min_priority")),
                    hidden=_parse_bool(row.get("hidden")),
                )

    _cache = rules
    return rules


def task_rule(title: str) -> TaskRule | None:
    if not title or title == "—":
        return None
    return load_task_rules().get(_title_key(title))


def description_override(title: str) -> str | None:
    rule = task_rule(title)
    if not rule or not rule.description:
        return None
    return rule.description


def title_override(title: str) -> str | None:
    rule = task_rule(title)
    if not rule or not rule.display_title:
        return None
    return rule.display_title


def _issue_priority_rank(issue: dict) -> int:
    key = (issue.get("priority") or {}).get("key", "")
    return PRIORITY_RANK.get(key, 99)


def should_show_task(issue: dict, normalized_title: str) -> bool:
    rule = task_rule(normalized_title)
    if not rule:
        return True
    if rule.hidden:
        return False
    if not rule.min_priority:
        return True
    min_rank = PRIORITY_RANK[rule.min_priority]
    return _issue_priority_rank(issue) <= min_rank
