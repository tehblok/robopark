from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MISSING = {"", "0", "-", "—", "n/a", "na", "n.a.", "none", "null", "нет", "не указан"}
DEFECT_RE = re.compile(r"\b([A-ZА-Я]{2,5})[\s_-]?(\d{1,3})\b", re.IGNORECASE)
ROBOT_RE = re.compile(r"^\s*\[\s*([AaАа]?\d{2,7})\s*]", re.IGNORECASE)
ERROR_RE = re.compile(
    r"\b(?:error|err|fault)[\s:_-]*([a-z0-9][a-z0-9_-]{1,40})", re.IGNORECASE
)
_GENERIC_ERROR_WORDS = {
    "or",
    "and",
    "error",
    "errors",
    "fault",
    "unknown",
    "none",
    "crit",
}
STRUCTURED_CODE_RE = re.compile(
    r"(?im)^\s*\**(?:message|machine[_ ]code|error[_ ]code|vhub status)\s*:\**\s*"
    r"([A-Za-z][A-Za-z0-9_.:/-]{2,100})"
)
TECH_TOKEN_RE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*(?:_timeout|_error|_fault|_failed|"
    r"_failure|_offline|_unavailable|_down|_mismatch|_invalid|_lost|_missing)\b",
    re.IGNORECASE,
)
HUD_PATH_RE = re.compile(
    r"(?im)^\s*(?:crit|error|warn):[^\n]{0,100}?(/[A-Za-z0-9_./@-]{3,})"
    r"(?::\s*|\s+)([A-Za-z][A-Za-z0-9_-]{0,127})(?![A-Za-z0-9_-])"
)


def stable_id(namespace: str, value: str, length: int = 24) -> str:
    digest = hashlib.sha256(f"{namespace}\0{value}".encode()).hexdigest()
    return f"{namespace}:{digest[:length]}"


def scalar(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("key", "display", "name", "id", "value"):
            if value.get(key) not in (None, ""):
                return str(value[key]).strip()
        return ""
    if isinstance(value, list):
        return ", ".join(filter(None, (scalar(item) for item in value)))
    return "" if value is None else str(value).strip()


def normalize_defect(value: Any) -> tuple[str | None, str | None]:
    raw = scalar(value)
    if raw.casefold().strip() in MISSING:
        return None, raw or None
    match = DEFECT_RE.search(raw)
    if not match:
        return None, raw
    return f"{match.group(1).upper()}-{int(match.group(2)):02d}", raw


def normalize_robot(summary: str) -> str | None:
    match = ROBOT_RE.search(summary)
    if not match:
        return None
    value = match.group(1).casefold().replace("а", "a")
    digits = value.removeprefix("a")
    return f"a{digits}"


def _load_component_labels() -> dict[str, str]:
    repository = Path(__file__).resolve().parents[2]
    source = repository / "apps/api/src/robopark_api/services/repair_guidance.py"
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "COMPONENT_LABELS"
                for target in node.targets
            ):
                value = ast.literal_eval(node.value)
                if isinstance(value, dict):
                    return {str(key): str(label) for key, label in value.items()}
    except (OSError, SyntaxError, ValueError):
        pass
    return {}


COMPONENT_LABELS = _load_component_labels()
_LABEL_TO_CODE = {label.casefold(): code for code, label in COMPONENT_LABELS.items()}


def normalize_components(values: Any) -> tuple[list[dict[str, str]], list[str]]:
    items = values if isinstance(values, list) else [values] if values else []
    components: dict[str, dict[str, str]] = {}
    unmapped: list[str] = []
    for item in items:
        raw = scalar(item)
        if not raw or raw == "ROBOT_UNSORTED":
            continue
        code = (
            raw
            if raw.startswith(("ROBOT_", "SOFTWARE_"))
            else _LABEL_TO_CODE.get(raw.casefold())
        )
        if code:
            components[code] = {"code": code, "label": COMPONENT_LABELS.get(code, raw)}
            if code not in COMPONENT_LABELS:
                unmapped.append(raw)
        else:
            unmapped.append(raw)
    return [components[key] for key in sorted(components)], sorted(set(unmapped))


def symptom_category(text: str) -> tuple[str, list[str]]:
    folded = text.casefold().replace("ё", "е")
    errors = []
    for match in ERROR_RE.finditer(text):
        raw = match.group(1)
        if raw.casefold() in _GENERIC_ERROR_WORDS:
            continue
        if not (
            any(character.isdigit() for character in raw)
            or "_" in raw
            or "-" in raw
            or raw.isupper()
        ):
            continue
        errors.append(f"ERROR_{raw.upper()}")
    errors = sorted(set(errors))
    if errors:
        return f"error:{errors[0]}", errors
    if "камер" in folded and any(
        term in folded for term in ("недоступ", "не отвеч", "нет сигнал", "offline")
    ):
        return "camera_unavailable", errors
    if any(term in folded for term in ("заряд", "акб", "батаре")):
        return "battery_charge", errors
    if "колес" in folded and any(
        term in folded for term in ("не крут", "не едет", "заклин", "привод")
    ):
        return "wheel_motion", errors
    if any(term in folded for term in ("не включ", "не запуска")):
        return "startup_failure", errors
    if any(term in folded for term in ("нет связи", "сеть", "модем", "интернет")):
        return "connectivity", errors
    if any(term in folded for term in ("перегрев", "температур")):
        return "overheating", errors
    if any(term in folded for term in ("шум", "скрип", "свист")):
        return "abnormal_noise", errors
    if any(term in folded for term in ("повреж", "сломан", "трещин")):
        return "physical_damage", errors
    cleaned = ROBOT_RE.sub("", folded)
    cleaned = re.sub(r"\s+by\s+[\w.-]+.*$", "", cleaned)
    cleaned = re.sub(r"\b(?:error|ошибка)\b", "", cleaned)
    cleaned = re.sub(r"[^a-zа-я0-9]+", " ", cleaned).strip()
    return (f"reported:{cleaned[:80]}" if cleaned else "reported:unknown"), errors


def extract_error_codes(text: str) -> list[str]:
    codes: set[str] = set()
    for match in STRUCTURED_CODE_RE.finditer(text):
        value = match.group(1).strip(" .:/-")
        if value.casefold() not in {"ok", "none", "unknown", "no_error", "no_errors"}:
            codes.add(value.upper())
    codes.update(match.group(0).upper() for match in TECH_TOKEN_RE.finditer(text))
    for match in HUD_PATH_RE.finditer(text):
        path, state = match.groups()
        if state.casefold() not in {"ok", "none", "unknown"}:
            codes.add(f"HUD:{path}:{state}".upper())
    return sorted(codes)[:32]


def names_from_people(values: Iterable[Any]) -> list[str]:
    result = []
    for value in values:
        if isinstance(value, dict):
            display = scalar(value.get("display"))
            if display:
                result.append(display)
    return result
