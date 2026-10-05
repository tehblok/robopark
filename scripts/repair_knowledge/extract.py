from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from scripts.build_knowledge_seed import Redactor, clean_text, clean_tracker_comment

from .normalize import (
    extract_error_codes,
    names_from_people,
    normalize_components,
    normalize_defect,
    normalize_robot,
    scalar,
    stable_id,
    symptom_category,
)

ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "CHANGE",
        re.compile(r"\b(?:заменил[аи]?|заменено|поменял[аи]?)\b", re.IGNORECASE),
    ),
    (
        "REPAIR",
        re.compile(
            r"\b(?:отремонтировал[аи]?|починил[аи]?|исправил[аи]?|восстановил[аи]?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "MAINTENANCE",
        re.compile(
            r"\b(?:обслужил[аи]?|смазал[аи]?|подтянул[аи]?|почистил[аи]?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "DIAG",
        re.compile(
            r"\b(?:проверил[аи]?|продиагностировал[аи]?|диагностика проведена)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "CONFIG",
        re.compile(
            r"\b(?:настроил[аи]?|перенастроил[аи]?|откалибровал[аи]?)\b", re.IGNORECASE
        ),
    ),
    ("HARD RESET", re.compile(r"\b(?:сбросил[аи]?|выполнен сброс)\b", re.IGNORECASE)),
    (
        "INSTALL",
        re.compile(
            r"\b(?:установил[аи]?\b|поставил[аи]?\s+(?:нов\w+\s+)?(?:камер\w*|колес\w*|"
            r"модул\w*|датчик\w*|акб\b|батаре\w*|лидар\w*|парктроник\w*|провод\w*|"
            r"контроллер\w*|крышк\w*|флаг\w*|шин\w*|резин\w*|замок\w*))",
            re.IGNORECASE,
        ),
    ),
    (
        "RESTART",
        re.compile(r"\b(?:перезапустил[аи]?|перезагрузил[аи]?)\b", re.IGNORECASE),
    ),
)
SUGGESTION_RE = re.compile(
    r"\b(?:нужно|надо|следует|рекоменду|можно|попробовать|требуется)\b", re.IGNORECASE
)
NEGATIVE_RE = re.compile(
    r"\b(?:не помог|не устран|ошибка остал|проблема остал|без результата|результата нет)\b",
    re.IGNORECASE,
)
RETURN_RE = re.compile(
    r"\b(?:ошибка|проблема|дефект).{0,30}\b(?:вернул|вернулась|повторил|снова)\b|\bснова.{0,30}(?:ошибка|проблема|дефект)\b",
    re.IGNORECASE,
)
VERIFIED_RE = re.compile(
    r"\b(?:проверка пройдена|тест пройден|проверил[аи] после|ошибка исчезла|"
    r"работает штатно|камера работает|ошибок (?:нет|не выявлено)|без ошибок|"
    r"тестовый проезд.{0,40}(?:успеш|без ошибок))\b",
    re.IGNORECASE,
)
VERIFICATION_NEGATION_RE = re.compile(
    r"\b(?:не работает|нестабил\w*|с перебоями|нештатно)\b", re.IGNORECASE
)
COMMENT_FIELD_RE = re.compile(r"(?im)^\s*\**(?:comment|комментарий)\s*:\**\s*(.+?)\s*$")
DETAIL_FIELD_RE = re.compile(r"(?im)(?:подробное описание)[^\n]*\n\s*(?![<{])([^\n]+)")
SUMMARY_ROBOT_RE = re.compile(r"^\s*\[[^]]+]\s*")


def _section(record: dict[str, Any], name: str) -> tuple[Any, bool]:
    section = (record.get("sections") or {}).get(name)
    if not isinstance(section, dict) or section.get("ok") is not True:
        return None, False
    return section.get("value"), True


def _iso(value: Any) -> str | None:
    text = scalar(value)
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            return None
    return text


def _negates_action(sentence: str, match: re.Match[str]) -> bool:
    before = sentence[max(0, match.start() - 32) : match.start()]
    return bool(re.search(r"\bне(?:\s+\w+){0,2}\s*$", before, re.IGNORECASE))


def _actions(
    text: str, evidence_ref: str, occurred_at: str | None
) -> list[dict[str, Any]]:
    actions = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", line):
            sentence = sentence.strip()
            if not sentence or SUGGESTION_RE.search(sentence):
                continue
            for method, pattern in ACTION_PATTERNS:
                match = pattern.search(sentence)
                if match and not _negates_action(sentence, match):
                    actions.append(
                        {
                            "method": method,
                            "evidence_ref": evidence_ref,
                            "occurred_at": occurred_at,
                            "excerpt": sentence[:280],
                        }
                    )
                    break
    return actions


def _reopened_refs(changelog: Any) -> list[str]:
    if not isinstance(changelog, list):
        return []
    closed = {"closed", "done", "resolved", "fixed"}
    reopened = {"open", "reopened", "inprogress", "in_progress", "queued", "repair"}
    refs = []
    for change in changelog:
        for field in change.get("fields") or [] if isinstance(change, dict) else []:
            meta = field.get("field") if isinstance(field, dict) else None
            if (
                scalar(meta).casefold() not in {"status", "статус"}
                and scalar(meta.get("id") if isinstance(meta, dict) else "").casefold()
                != "status"
            ):
                continue
            if (
                scalar(field.get("from")).casefold() in closed
                and scalar(field.get("to")).casefold() in reopened
            ):
                refs.append(
                    f"changelog:{scalar(change.get('id')) or stable_id('change', scalar(change.get('updatedAt')))}"
                )
    return sorted(set(refs))


def _outcome_refs(
    evidence: list[dict[str, Any]], observed: list[dict[str, Any]]
) -> tuple[list[str], list[str], list[str]]:
    action_refs = {action["evidence_ref"] for action in observed}
    seen_action = False
    verified: set[str] = set()
    negative: set[str] = set()
    returned: set[str] = set()
    for item in evidence:
        if not item["ref"].startswith("comment:"):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", item["text"]):
            action_here = bool(_actions(sentence, item["ref"], item["occurred_at"]))
            verification = VERIFIED_RE.search(sentence)
            if verification and not VERIFICATION_NEGATION_RE.search(sentence):
                action_starts = [
                    match.start()
                    for _, pattern in ACTION_PATTERNS
                    if (match := pattern.search(sentence))
                    and not _negates_action(sentence, match)
                ]
                if seen_action or (
                    action_starts and min(action_starts) < verification.start()
                ):
                    verified.add(item["ref"])
            if (seen_action or action_here) and NEGATIVE_RE.search(sentence):
                negative.add(item["ref"])
            if (seen_action or action_here) and RETURN_RE.search(sentence):
                returned.add(item["ref"])
            if action_here and item["ref"] in action_refs:
                seen_action = True
    return sorted(verified), sorted(negative), sorted(returned)


def extract_case(record: dict[str, Any], redactor: Redactor) -> dict[str, Any]:
    issue_value, issue_ok = _section(record, "issue")
    issue = issue_value if isinstance(issue_value, dict) else {}
    key = scalar(issue.get("key") or record.get("key")).upper()
    if not key:
        raise ValueError("record has no issue key")
    comments_value, comments_ok = _section(record, "comments")
    comments = comments_value if isinstance(comments_value, list) else []
    changelog, changelog_ok = _section(record, "changelog")
    links_value, links_ok = _section(record, "links")

    people = [
        issue.get(name) for name in ("assignee", "resolvedBy", "createdBy", "updatedBy")
    ]
    people.extend(
        comment.get("createdBy") for comment in comments if isinstance(comment, dict)
    )
    names = names_from_people(people)
    summary = redactor.redact(clean_text(issue.get("summary")), names)[:500]
    description = redactor.redact(
        clean_tracker_comment(issue.get("description")), names
    )[:1500]
    components, unmapped = normalize_components(issue.get("components"))
    non_robot_domain = sorted(
        name for name in unmapped if name.startswith(("CAR_", "TRUCK_"))
    )
    summary_subject = SUMMARY_ROBOT_RE.sub("", summary).strip()
    component_subjects = {
        value.casefold()
        for component in components
        for value in (component["code"], component["label"])
    }
    is_template = bool(
        re.search(r"\s+by(?:\s+[\w.-]+)?\s*$", summary_subject, re.IGNORECASE)
    )
    is_template = is_template or summary_subject.casefold() in component_subjects
    comment_match = COMMENT_FIELD_RE.search(description)
    detail_match = DETAIL_FIELD_RE.search(description)
    template_detail = comment_match.group(1).strip()[:500] if comment_match else ""
    if not template_detail and detail_match:
        template_detail = detail_match.group(1).strip()[:500]
    summary_words = re.findall(r"[A-Za-zА-Яа-я0-9]+", summary_subject)
    summary_has_problem = bool(
        re.search(
            r"(?i)\b(?:не|нет|ошиб|error|fault|слом|повреж|недоступ|проблем|шум|скрип|свист|теч)\w*",
            summary_subject,
        )
    )
    if template_detail and len(summary_words) <= 2 and not summary_has_problem:
        is_template = True
    symptom_excerpt = template_detail if is_template and template_detail else summary
    category, summary_error_codes = symptom_category(symptom_excerpt)
    error_codes = sorted(
        set(summary_error_codes) | set(extract_error_codes(description))
    )
    if error_codes:
        category = f"error:{error_codes[0]}"
    symptom = "\n".join(filter(None, (summary, description))).strip()
    defect_value = next(
        (
            value
            for field, value in issue.items()
            if str(field).endswith("theDefectCode")
        ),
        issue.get("defectCode"),
    )
    defect_code, defect_raw = normalize_defect(defect_value)

    evidence: list[dict[str, Any]] = []
    if description:
        evidence.append(
            {
                "ref": "issue:description",
                "text": description,
                "occurred_at": _iso(issue.get("createdAt")),
            }
        )
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        text = redactor.redact(
            clean_tracker_comment(comment.get("text") or comment.get("display")), names
        )
        if text:
            evidence.append(
                {
                    "ref": f"comment:{scalar(comment.get('id') or comment.get('longId')) or stable_id('comment', text)}",
                    "text": text[:1200],
                    "occurred_at": _iso(comment.get("createdAt")),
                }
            )
    observed: list[dict[str, Any]] = []
    seen_actions: set[tuple[str, str, str]] = set()
    for item in evidence:
        for action in _actions(item["text"], item["ref"], item["occurred_at"]):
            marker = (action["method"], action["evidence_ref"], action["excerpt"])
            if marker not in seen_actions:
                seen_actions.add(marker)
                observed.append(action)
    verified_refs, negative_refs, return_refs = _outcome_refs(evidence, observed)
    reopened_refs = _reopened_refs(changelog)
    outcome = {
        "explicitly_verified": bool(verified_refs),
        "negative": bool(negative_refs),
        "reported_return": bool(return_refs),
        "reopened": bool(reopened_refs),
        "evidence_refs": {
            "explicitly_verified": verified_refs,
            "negative": negative_refs,
            "reported_return": return_refs,
            "reopened": reopened_refs,
        },
    }
    quality = []
    if not issue_ok:
        quality.append("missing_issue_section")
    if not comments_ok:
        quality.append("missing_comments_section")
    if not changelog_ok:
        quality.append("missing_changelog_section")
    if not links_ok:
        quality.append("missing_links_section")
    if not symptom:
        quality.append("missing_symptom")
    if category in {
        "reported:other",
        "reported:logs upload",
        "reported:service",
        "reported:repair",
        "reported:unknown",
    }:
        quality.append("generic_symptom")
    if is_template and not template_detail:
        quality.append("generic_symptom")
    if non_robot_domain:
        quality.append("non_robot_domain_component")
    if not components:
        quality.append("missing_component")
    if unmapped:
        quality.append("unmapped_component")
    if not defect_code:
        quality.append("missing_defect_code")
    if not observed:
        quality.append("missing_observed_action")

    related = set()
    duplicate_refs = set()
    if isinstance(links_value, list):
        for link in links_value:
            if isinstance(link, dict):
                linked = scalar(link.get("object"))
                if linked and linked != key:
                    link_type = (
                        link.get("type") if isinstance(link.get("type"), dict) else {}
                    )
                    type_text = " ".join(
                        scalar(link_type.get(field))
                        for field in ("id", "display", "inward", "outward")
                    ).casefold()
                    if "duplicate" in type_text or "дубликат" in type_text:
                        duplicate_refs.add(linked.upper())
                    else:
                        related.add(linked)
    return {
        "schema": 1,
        "case_id": stable_id("repair-case:v1", key),
        "issue_key": key,
        "created_at": _iso(issue.get("createdAt")),
        "resolved_at": _iso(issue.get("resolvedAt")),
        "robot_id": normalize_robot(summary),
        "symptom": symptom[:1800],
        "symptom_excerpt": symptom_excerpt[:500],
        "symptom_category": category,
        "error_codes": error_codes,
        "components": components,
        "unmapped_components": unmapped,
        "non_robot_domain_components": non_robot_domain,
        "defect_code": defect_code,
        "defect_raw": defect_raw,
        "declared_method": scalar(issue.get("solutionMethod")).upper() or None,
        "observed_actions": observed,
        "outcome": outcome,
        "status": scalar(issue.get("statusType")) or None,
        "resolution": scalar(issue.get("resolution")) or None,
        "evidence_refs": [item["ref"] for item in evidence],
        "related_issue_refs_noncausal": sorted(related),
        "duplicate_issue_refs": sorted(duplicate_refs),
        "possible_repeat_30d": False,
        "quality_flags": sorted(set(quality)),
        "training_eligible": False,
        "review_required": True,
    }
