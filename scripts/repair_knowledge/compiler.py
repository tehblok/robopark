from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.build_knowledge_seed import Redactor

from .extract import extract_case
from .normalize import stable_id

OUTPUT_FILES = (
    "cases.jsonl",
    "cards.jsonl",
    "rag-seed.jsonl",
    "taxonomy.json",
    "report.json",
)
MAX_RECORD_BYTES = 64 * 1024 * 1024


def _validate_paths(source: Path, output: Path) -> Path:
    if source.is_symlink():
        raise ValueError("source must be a real directory, not a symlink")
    source = source.resolve()
    if not source.is_dir():
        raise ValueError("source must be a real directory, not a symlink")
    records = source / "records"
    if not records.is_dir() or records.is_symlink():
        raise ValueError("records must be a real directory, not a symlink")
    output_absolute = output.absolute()
    if output_absolute.is_symlink():
        raise ValueError("output must not be a symlink")
    output_resolved = output_absolute.resolve()
    if (
        output_resolved == source
        or source in output_resolved.parents
        or output_resolved in source.parents
    ):
        raise ValueError("source and output must not overlap")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    for name in OUTPUT_FILES:
        if (output / name).is_symlink():
            raise ValueError("output file must not be a symlink")
    return records


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            return None


def _mark_repeats(cases: list[dict[str, Any]]) -> None:
    previous: dict[
        tuple[str, tuple[str, ...], str | None, str], list[tuple[datetime, str]]
    ] = defaultdict(list)
    for case in sorted(cases, key=lambda item: item.get("created_at") or ""):
        robot = case.get("robot_id")
        when = _parse_time(case.get("created_at"))
        if not robot or when is None:
            continue
        fingerprint = (
            robot,
            tuple(component["code"] for component in case["components"]),
            case.get("defect_code"),
            case["symptom_category"],
        )
        case["possible_repeat_30d"] = any(
            incident != case["incident_id"] and 0 <= (when - old).days <= 30
            for old, incident in previous[fingerprint]
        )
        previous[fingerprint].append((when, case["incident_id"]))


def _assign_incidents(cases: list[dict[str, Any]]) -> int:
    by_key = {case["issue_key"]: case for case in cases}
    parent = {key: key for key in by_key}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for case in cases:
        for linked in case["duplicate_issue_refs"]:
            if linked in by_key:
                union(case["issue_key"], linked)
    roots = set()
    for case in cases:
        root = find(case["issue_key"])
        roots.add(root)
        case["incident_id"] = stable_id("repair-incident:v1", root)
    return len(cases) - len(roots)


def _card_key(case: dict[str, Any]) -> tuple[tuple[str, ...], str, str]:
    return (
        tuple(component["code"] for component in case["components"]),
        case.get("defect_code") or "UNKNOWN",
        case["symptom_category"],
    )


def _build_cards(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[tuple[str, ...], str, str], list[dict[str, Any]]] = defaultdict(
        list
    )
    for case in cases:
        if (
            case["symptom"]
            and case["components"]
            and case["observed_actions"]
            and "generic_symptom" not in case["quality_flags"]
            and "non_robot_domain_component" not in case["quality_flags"]
        ):
            groups[_card_key(case)].append(case)
    cards = []
    for key, incidents in sorted(groups.items()):
        unique = {case["incident_id"]: case for case in incidents}
        incidents = list(unique.values())
        if len(incidents) < 3:
            continue
        component_codes, defect, category = key
        method_counts = Counter(
            method
            for case in incidents
            for method in {action["method"] for action in case["observed_actions"]}
        )
        declared_counts = Counter(
            case["declared_method"] for case in incidents if case.get("declared_method")
        )
        outcome_counts = Counter()
        error_code_incident_counts = Counter(
            code for case in incidents for code in set(case["error_codes"])
        )
        for case in incidents:
            for name in (
                "explicitly_verified",
                "negative",
                "reported_return",
                "reopened",
            ):
                if case["outcome"][name]:
                    outcome_counts[name] += 1
        labels = {
            component["code"]: component["label"]
            for case in incidents
            for component in case["components"]
        }
        card_id = stable_id(
            "repair-card:v1", json.dumps(key, ensure_ascii=False, separators=(",", ":"))
        )
        primary_error_code = (
            category.removeprefix("error:") if category.startswith("error:") else None
        )
        primary_error_codes = (
            [primary_error_code]
            if primary_error_code in error_code_incident_counts
            else []
        )
        cards.append(
            {
                "schema": 1,
                "card_id": card_id,
                "components": [
                    {"code": code, "label": labels[code]} for code in component_codes
                ],
                "defect_code": None if defect == "UNKNOWN" else defect,
                "defect_unknown": defect == "UNKNOWN",
                "symptom_category": category,
                "error_codes": primary_error_codes,
                "error_code_incident_counts": dict(
                    sorted(error_code_incident_counts.items())
                ),
                "supporting_incidents": len(incidents),
                "distinct_robots": len(
                    {case["robot_id"] for case in incidents if case.get("robot_id")}
                ),
                "method_counts": dict(sorted(method_counts.items())),
                "declared_method_counts": dict(sorted(declared_counts.items())),
                "outcome_counts": {
                    name: outcome_counts.get(name, 0)
                    for name in (
                        "explicitly_verified",
                        "negative",
                        "reported_return",
                        "reopened",
                    )
                },
                "contradictions": {
                    "multiple_observed_methods": len(method_counts) > 1,
                    "mixed_outcomes": outcome_counts["explicitly_verified"] > 0
                    and (
                        outcome_counts["negative"] > 0
                        or outcome_counts["reported_return"] > 0
                        or outcome_counts["reopened"] > 0
                    ),
                },
                "examples": [
                    {
                        "symptom": case["symptom_excerpt"][:180],
                        "reported_actions": [
                            action["excerpt"][:160]
                            for action in case["observed_actions"][:2]
                        ],
                    }
                    for case in incidents[:3]
                ],
                "evidence_case_ids": sorted(case["case_id"] for case in incidents),
                "limitations": [
                    "Это агрегат сообщений о работах, а не проверенная инструкция и не доказательство причинности.",
                    "Выгрузка содержит закрытые задачи: по этим счетчикам нельзя вычислять эффективность или вероятность успеха.",
                    "Связи Tracker не считаются причинными; перед применением требуется проверка механиком.",
                ],
                "training_eligible": False,
                "review_required": True,
            }
        )
    return cards


def _rag(card: dict[str, Any]) -> dict[str, str] | None:
    category_labels = {
        "camera_unavailable": "камера недоступна",
        "battery_charge": "проблема заряда или аккумулятора",
        "wheel_motion": "проблема движения колеса",
        "startup_failure": "устройство не запускается",
        "connectivity": "проблема связи",
        "overheating": "перегрев или отклонение температуры",
        "abnormal_noise": "посторонний шум",
        "physical_damage": "механическое повреждение",
    }
    raw_category = card["symptom_category"]
    if raw_category not in category_labels:
        if not raw_category.startswith("error:"):
            return None
        technical = raw_category.removeprefix("error:")
        if technical not in card["error_codes"] or not re.fullmatch(
            r"[A-Z0-9_./:@-]{3,180}", technical
        ):
            return None
    component_text = ", ".join(
        f"{item['label']} ({item['code']})" for item in card["components"]
    )
    defect = card["defect_code"] or "не указан"
    methods = ", ".join(
        f"{method}: {count}" for method, count in card["method_counts"].items()
    )
    outcomes = ", ".join(
        f"{name}: {count}" for name, count in card["outcome_counts"].items()
    )
    error_codes = ", ".join(card["error_codes"]) or "не выделены"
    category = category_labels.get(
        raw_category, f"техническая ошибка {raw_category.removeprefix('error:')}"
    )
    content = (
        f"Наблюдаемая категория: {category}\n"
        f"Коды ошибок из текста: {error_codes}\n"
        f"Компоненты: {component_text}\nКод дефекта: {defect}\n"
        f"Независимых задач: {card['supporting_incidents']}; разных роботов: {card['distinct_robots']}\n"
        f"Заявленные выполненные действия (частота, не эффективность): {methods}\n"
        f"Наблюдаемые исходы: {outcomes}\n"
        "Ограничение: это непроверенный агрегат сообщений. Он помогает искать варианты диагностики, "
        "но не является инструкцией и требует подтверждения механиком."
    )
    return {
        "title": f"Ремонтный опыт: {component_text} · {defect}"[:250],
        "content": content,
        "kind": "note",
        "source_ref": card["card_id"],
    }


def _taxonomy(cases: list[dict[str, Any]]) -> dict[str, Any]:
    components = Counter(
        component["code"] for case in cases for component in case["components"]
    )
    labels = {
        component["code"]: component["label"]
        for case in cases
        for component in case["components"]
    }
    defects = Counter(case["defect_code"] or "unknown" for case in cases)
    declared = Counter(case["declared_method"] or "unknown" for case in cases)
    observed = Counter(
        action["method"] for case in cases for action in case["observed_actions"]
    )
    unmapped = Counter(name for case in cases for name in case["unmapped_components"])
    return {
        "schema": 1,
        "components": [
            {"code": code, "label": labels[code], "incidents": count}
            for code, count in sorted(components.items())
        ],
        "defect_codes": dict(sorted(defects.items())),
        "declared_methods": dict(sorted(declared.items())),
        "observed_methods": dict(sorted(observed.items())),
        "unmapped_component_names": dict(sorted(unmapped.items())),
        "constraints_inferred": False,
    }


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    payload = "".join(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        for value in values
    )
    _write_text(path, payload)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_text(
        path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _write_text(path: Path, payload: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() and temporary.is_symlink():
        raise ValueError("temporary output file must not be a symlink")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def compile_export(source: Path, output: Path) -> dict[str, Any]:
    """Compile one exported records directory without changing its inputs."""
    source, output = Path(source), Path(output)
    records = _validate_paths(source, output)
    redactor = Redactor()
    cases: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    invalid: list[dict[str, str]] = []
    duplicates = 0
    for path in sorted(records.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            raise ValueError(f"record symlink is forbidden: {path.name}")
        if not path.is_file() or path.suffix.casefold() != ".json":
            continue
        if path.stat().st_size > MAX_RECORD_BYTES:
            invalid.append({"file": path.name, "error": "record_too_large"})
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise TypeError("record is not an object")
            case = extract_case(record, redactor)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
        ) as error:
            invalid.append({"file": path.name, "error": type(error).__name__})
            continue
        if case["issue_key"] in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(case["issue_key"])
        cases.append(case)
    duplicate_link_records = _assign_incidents(cases)
    _mark_repeats(cases)
    cases.sort(key=lambda item: item["issue_key"])
    cards = _build_cards(cases)
    rag = [document for card in cards if (document := _rag(card)) is not None]
    report = {
        "schema": 1,
        "source_records": len(
            [
                path
                for path in records.iterdir()
                if path.is_file() and path.suffix.casefold() == ".json"
            ]
        ),
        "records_compiled": len(cases),
        "records_duplicate": duplicates,
        "records_clustered_by_duplicate_links": duplicate_link_records,
        "records_invalid": len(invalid),
        "cases_with_error_codes": sum(bool(case["error_codes"]) for case in cases),
        "unique_error_codes": len(
            {code for case in cases for code in case["error_codes"]}
        ),
        "invalid_records": invalid[:100],
        "cards": len(cards),
        "rag_documents": len(rag),
        "cases_review_required": sum(bool(case["review_required"]) for case in cases),
        "cases_training_eligible": sum(
            bool(case["training_eligible"]) for case in cases
        ),
        "quality_flags": dict(
            sorted(
                Counter(
                    flag for case in cases for flag in case["quality_flags"]
                ).items()
            )
        ),
        "redactions": dict(sorted(redactor.counts.items())),
        "selection_bias": "Источник состоит из выгруженных задач; доли исходов и счетчики действий не являются оценкой эффективности.",
    }
    _write_jsonl(output / "cases.jsonl", cases)
    _write_jsonl(output / "cards.jsonl", cards)
    _write_jsonl(output / "rag-seed.jsonl", rag)
    _write_json(output / "taxonomy.json", _taxonomy(cases))
    _write_json(output / "report.json", report)
    return report
