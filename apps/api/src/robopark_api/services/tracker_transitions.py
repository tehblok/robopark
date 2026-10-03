"""Deterministic matching of semantic lifecycle purposes to Tracker transitions."""

from __future__ import annotations

import re
from typing import Literal

TransitionPurpose = Literal["start", "review", "diagnostics", "return", "close"]

_ALIASES: dict[TransitionPurpose, tuple[str, ...]] = {
    "start": (
        "in progress",
        "inprogress",
        "start",
        "start work",
        "в работе",
        "в работу",
        "начать работу",
    ),
    "review": (
        "review",
        "verification",
        "verify",
        "проверка",
        "на проверку",
        "передать на проверку",
    ),
    "diagnostics": ("diagnostics", "diagnostic", "диагностика", "на диагностику"),
    "return": (
        "return",
        "return to work",
        "reopen",
        "вернуть",
        "вернуть в работу",
        "возврат в работу",
    ),
    "close": (
        "close",
        "closed",
        "resolve",
        "resolved",
        "done",
        "закрыть",
        "закрыто",
        "завершить",
        "завершено",
        "решить",
        "решено",
    ),
}

# Some Tracker queues cannot move directly from inspection back to "В работе".
# Returning to their queue is the available nonterminal path for the same
# mechanic-owned repair cycle. Prefer a direct return when it exists.
_RETURN_QUEUE_ALIASES = ("queued", "queue", "в очереди", "очередь")

_TARGET_STATUS_ALIASES: dict[TransitionPurpose, tuple[str, ...]] = {
    "start": ("in progress", "inprogress", "в работе"),
    "review": (
        "review",
        "verification",
        "проверка",
        "проверка оператором",
        "на проверке",
    ),
    "diagnostics": ("diagnostics", "diagnostic", "диагностика", "на диагностике"),
    "return": (
        "in progress",
        "inprogress",
        "в работе",
        "queued",
        "queue",
        "в очереди",
        "очередь",
    ),
    "close": (
        "close",
        "closed",
        "resolve",
        "resolved",
        "done",
        "закрыто",
        "завершено",
        "решено",
    ),
}


def normalize_transition_name(value: object) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def _score(value: object, aliases: tuple[str, ...]) -> int:
    normalized = normalize_transition_name(value)
    if not normalized:
        return 0
    normalized_aliases = tuple(normalize_transition_name(alias) for alias in aliases)
    if normalized in normalized_aliases:
        return 100
    tokens = set(normalized.split())
    matches = [alias for alias in normalized_aliases if set(alias.split()) <= tokens]
    if not matches:
        return 0
    return 10 + max(len(alias.split()) for alias in matches)


def resolve_transition(transitions: list[dict], purpose: TransitionPurpose) -> str | None:
    """Return the only best semantic match, never an arbitrary transition."""
    alias_groups = (
        (_ALIASES[purpose], _RETURN_QUEUE_ALIASES) if purpose == "return" else (_ALIASES[purpose],)
    )
    for aliases in alias_groups:
        exact_only = purpose == "return" and aliases == _RETURN_QUEUE_ALIASES
        ranked: list[tuple[int, str]] = []
        for transition in transitions:
            transition_id = str(transition.get("id") or "").strip()
            if not transition_id:
                continue
            score = max(
                _score(transition_id, aliases),
                _score(transition.get("display"), aliases),
            )
            if score and (not exact_only or score == 100):
                ranked.append((score, transition_id))
        if ranked:
            best_score = max(score for score, _ in ranked)
            best = {transition_id for score, transition_id in ranked if score == best_score}
            return next(iter(best)) if len(best) == 1 else None
    return None


def target_status_reached(issue: dict, purpose: TransitionPurpose) -> bool:
    """Recognize the intended target status before replaying a leased transition."""
    aliases = _TARGET_STATUS_ALIASES[purpose]
    return any(_score(issue.get(field), aliases) == 100 for field in ("status", "status_key"))
