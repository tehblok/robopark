"""Validated Tracker fields used by the short repair workflow."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from robopark_api.services.defect_codes import validate_defect_code

DEFECT_FIELD_ID = "60df26695151a36df681d67b--theDefectCode"
SOLUTION_METHOD_FIELD_ID = "solutionMethod"
MAX_COMPONENTS = 500

SOLUTION_METHODS = (
    ("CHANGE", "Заменил"),
    ("REPAIR", "Отремонтировал"),
    ("MAINTENANCE", "Обслужил"),
    ("DIAG", "Провёл диагностику"),
    ("CONFIG", "Настроил"),
    ("HARD RESET", "Сбросил настройки"),
    ("INSTALL", "Установил"),
    ("RESTART", "Перезапустил"),
)
_SOLUTION_LABELS = dict(SOLUTION_METHODS)


def validate_solution_method(value: str) -> str:
    if value not in _SOLUTION_LABELS:
        raise HTTPException(422, "solution_method_invalid")
    return value


def field_snapshot(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        "component_ids": sorted(str(value) for value in issue.get("component_ids") or []),
        "defect_code": str(issue.get("defect_code") or "") or None,
        "solution_method": str(issue.get("solution_method") or "") or None,
    }


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def exact_title_matches(summary: str, components: list[dict[str, str]]) -> list[str]:
    title = f" {_normalized_words(summary)} "
    matches = [
        item["id"]
        for item in components
        if item["label"] and f" {_normalized_words(item['label'])} " in title
    ]
    return matches if len(matches) == 1 else []


def options(issue: dict[str, Any], components: list[dict[str, str]]) -> dict[str, Any]:
    selected = field_snapshot(issue)
    suggested = (
        []
        if selected["component_ids"]
        else exact_title_matches(str(issue.get("summary") or ""), components)
    )
    defect = selected["defect_code"]
    try:
        recognized_defect = validate_defect_code(defect) if defect else None
    except HTTPException:
        recognized_defect = None
    solution = selected["solution_method"]
    recognized_solution = solution if solution in _SOLUTION_LABELS else None
    return {
        "issue_key": str(issue.get("key") or ""),
        "components": components,
        "selected_component_ids": selected["component_ids"],
        "suggested_component_ids": suggested,
        "suggestion_reason": "exact_title_match" if suggested else None,
        "defect_code": recognized_defect,
        "solution_method": recognized_solution,
        "solution_methods": [{"code": code, "label": label} for code, label in SOLUTION_METHODS],
        "field_snapshot": selected,
    }


def resolve_claim_components(
    issue: dict[str, Any],
    components: list[dict[str, str]],
    requested: list[str] | None,
) -> list[str] | None:
    if issue.get("component_ids") or issue.get("components"):
        return None
    selected = (
        requested
        if requested is not None
        else exact_title_matches(str(issue.get("summary") or ""), components)
    )
    if not selected:
        raise HTTPException(409, "task_component_selection_required")
    if len(selected) != len(set(selected)):
        raise HTTPException(422, "task_component_invalid")
    active = {item["id"] for item in components}
    if any(value not in active for value in selected):
        raise HTTPException(422, "task_component_invalid")
    return selected


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    component_ids = payload.get("component_ids")
    expected = payload.get("expected")
    if (
        not isinstance(component_ids, list)
        or not component_ids
        or len(component_ids) > 20
        or not all(isinstance(value, str) and value.strip() for value in component_ids)
        or len(component_ids) != len(set(component_ids))
        or not isinstance(expected, dict)
    ):
        raise HTTPException(422, "repair_fields_invalid")
    expected_components = expected.get("component_ids")
    if not isinstance(expected_components, list) or not all(
        isinstance(value, str) and value.strip() for value in expected_components
    ):
        raise HTTPException(422, "repair_fields_invalid")
    defect_code = validate_defect_code(str(payload.get("defect_code") or ""))
    solution_method = validate_solution_method(str(payload.get("solution_method") or ""))
    return {
        "component_ids": sorted(component_ids),
        "defect_code": defect_code,
        "solution_method": solution_method,
        "expected": {
            "component_ids": sorted(expected_components),
            "defect_code": str(expected.get("defect_code") or "") or None,
            "solution_method": str(expected.get("solution_method") or "") or None,
        },
    }


def validate_component_selection(payload: dict[str, Any], components: list[dict[str, str]]) -> None:
    allowed = {item["id"] for item in components} | set(payload["expected"]["component_ids"])
    if any(value not in allowed for value in payload["component_ids"]):
        raise HTTPException(422, "task_component_invalid")


def report_text(payload: dict[str, Any], components: list[dict[str, str]]) -> str:
    labels = {item["id"]: item["label"] for item in components}
    component_text = ", ".join(labels.get(value, value) for value in payload["component_ids"])
    return (
        f"Выполненные работы\nКомпоненты: {component_text}\n"
        f"Неисправность: {payload['defect_code']}\n"
        f"Действие: {_SOLUTION_LABELS[payload['solution_method']]}"
    )
