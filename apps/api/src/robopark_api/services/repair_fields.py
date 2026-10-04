"""Validated Tracker fields used by the short repair workflow."""

from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from robopark_api.services.defect_codes import validate_defect_code
from robopark_api.services.repair_guidance import (
    DEFECT_METHOD_SUGGESTIONS,
    decorate_components,
)

DEFECT_FIELD_ID = "60df26695151a36df681d67b--theDefectCode"
SOLUTION_METHOD_FIELD_ID = "solutionMethod"
MAX_COMPONENTS = 500
TEMPORARY_COMPONENT_NAME = "ROBOT_UNSORTED"

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


def is_temporary_component_option(component: dict[str, Any]) -> bool:
    return any(
        str(component.get(field) or "").strip() == TEMPORARY_COMPONENT_NAME
        for field in ("id", "label", "tracker_name")
    )


def temporary_component_ids(
    issue: dict[str, Any], components: list[dict[str, Any]] | None = None
) -> set[str]:
    result = {
        str(item.get("id") or "").strip()
        for item in components or []
        if is_temporary_component_option(item)
    }
    result.discard("")
    issue_ids = [str(value) for value in issue.get("component_ids") or []]
    issue_labels = [str(value) for value in issue.get("components") or []]
    for index, label in enumerate(issue_labels):
        if label.strip() == TEMPORARY_COMPONENT_NAME and index < len(issue_ids):
            result.add(issue_ids[index])
    result.update(value for value in issue_ids if value.strip() == TEMPORARY_COMPONENT_NAME)
    return result


def has_temporary_component(issue: dict[str, Any]) -> bool:
    return bool(temporary_component_ids(issue)) or any(
        str(value).strip() == TEMPORARY_COMPONENT_NAME for value in issue.get("components") or []
    )


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def exact_title_matches(summary: str, components: list[dict[str, str]]) -> list[str]:
    title = f" {_normalized_words(summary)} "
    matches = []
    for item in components:
        terms = [
            str(item.get("label") or ""),
            str(item.get("tracker_name") or ""),
        ]
        if any(term and f" {_normalized_words(term)} " in title for term in terms):
            matches.append(item["id"])
    matches = list(dict.fromkeys(matches))
    return matches if len(matches) == 1 else []


def options(issue: dict[str, Any], components: list[dict[str, str]]) -> dict[str, Any]:
    selected = field_snapshot(issue)
    temporary_ids = temporary_component_ids(issue, components)
    selectable = decorate_components(
        [item for item in components if not is_temporary_component_option(item)]
    )
    selected_ids = [value for value in selected["component_ids"] if value not in temporary_ids]
    suggested = (
        [] if selected_ids else exact_title_matches(str(issue.get("summary") or ""), selectable)
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
        "components": selectable,
        "selected_component_ids": selected_ids,
        "suggested_component_ids": suggested,
        "suggestion_reason": "exact_title_match" if suggested else None,
        "defect_code": recognized_defect,
        "solution_method": recognized_solution,
        "solution_methods": [{"code": code, "label": label} for code, label in SOLUTION_METHODS],
        "defect_method_suggestions": {
            code: list(methods) for code, methods in DEFECT_METHOD_SUGGESTIONS.items()
        },
        "field_snapshot": selected,
    }


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


def validate_component_selection(
    payload: dict[str, Any],
    components: list[dict[str, str]],
    *,
    current_issue: dict[str, Any] | None = None,
) -> None:
    allowed = {item["id"] for item in components} | set(payload["expected"]["component_ids"])
    temporary_ids = (
        {item["id"] for item in components if is_temporary_component_option(item)}
        | temporary_component_ids(current_issue or {}, components)
        | {TEMPORARY_COMPONENT_NAME}
    )
    if any(value not in allowed or value in temporary_ids for value in payload["component_ids"]):
        raise HTTPException(422, "task_component_invalid")


def report_text(payload: dict[str, Any], components: list[dict[str, str]]) -> str:
    labels = {item["id"]: item["label"] for item in decorate_components(components)}
    component_text = ", ".join(labels.get(value, value) for value in payload["component_ids"])
    return (
        f"Выполненные работы\nКомпоненты: {component_text}\n"
        f"Неисправность: {payload['defect_code']}\n"
        f"Действие: {_SOLUTION_LABELS[payload['solution_method']]}"
    )
