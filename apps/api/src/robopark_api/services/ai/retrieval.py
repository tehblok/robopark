"""Bounded lexical reranking; relevance is not a probability of a correct repair."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

# Keep the stored v1 vocabulary unchanged. Expand queries instead of silently
# requiring a full index rebuild on an existing installation.
ALIASES = {
    "camera": ("камера", "камеры", "camera", "камер"),
    "lidar": ("лидар", "лидара", "lidar"),
    "battery": ("акб", "аккумулятор", "батарея", "battery"),
    "ultrasonic": ("парктроник", "парктроники", "ultrasonic"),
    "motorcontrol": (
        "motorcontrol",
        "motor control",
        "моторконтрол",
        "моторконтроллер",
        "мотор контроллер",
    ),
    "pcu": ("pcu",),
    "brain": ("brain",),
    "wheel": ("колесо", "колеса", "колёс", "wheel", "wheels", "моторколесо"),
    "spring": ("рессора", "рессоры", "spring"),
    "wire": ("провод", "кабель", "кабеля", "коса", "проводка", "wire", "cable"),
    "lid": ("крышка", "крышки", "bootlid"),
    "lock": ("замок", "замка", "замки", "lock"),
    "flag": ("флаг", "флажок", "флажка", "flag"),
    "modem": ("модем", "modem", "gated1"),
}
NOISE = {
    "робот",
    "роботa",
    "ровер",
    "rover",
    "r3",
    "нужно",
    "надо",
    "можно",
    "нужн",
    "сделат",
    "делат",
    "проверит",
    "проверя",
    "какой",
    "како",
    "каким",
    "каких",
    "котор",
    "так",
    "там",
    "тут",
    "этот",
    "этом",
    "если",
    "чтобы",
    "после",
    "перед",
    "будет",
    "был",
    "было",
    "есть",
    "нет",
    "работает",
    "работа",
    "работ",
    "проблем",
    "ремонт",
    "ошибк",
    "вопрос",
    "пожалуйста",
    "ним",
    "ней",
    "ему",
    "мне",
    "нам",
    "почему",
    "исправит",
    "починит",
    "предпринят",
    "подскажит",
    "инструкц",
    "инструкци",
    "заменит",
    "замен",
    "заменят",
    "снят",
    "снимат",
    "снять",
    "сним",
    "установит",
    "установк",
    "установ",
    "проверка",
    "проверк",
    "правильно",
    "безопасно",
}
POSITIONS = (("лев", "прав"), ("передн", "задн"))
CODE = re.compile(r"\b([a-z]{2,4})[-– ]?(\d{1,4})\b", re.I)


def codes(text):
    return {a.lower() + b for a, b in CODE.findall(text)}


@lru_cache(maxsize=4)
def alias_terms(tokenize):
    return {key: set(tokenize(" ".join(values))) for key, values in ALIASES.items()}


def canonical(text, tokenize):
    # Joining this compound prevents generic 'motor' from matching another device.
    text = re.sub(r"\bmotor\s+control\b|мотор[- ]*контрол\w*", "motorcontrol", text, flags=re.I)
    words = set(tokenize(text))
    aliases = alias_terms(tokenize)
    result = set(words)
    for key, variants in aliases.items():
        if words & variants:
            result.difference_update(variants)
            result.add(key)
    return result


@dataclass
class Query:
    text: str
    terms: set[str]
    keys: set[str]
    entities: set[str]
    exact_codes: set[str]
    procedure: bool
    mapping: bool


def prepare(text, tokenize, context=""):
    meaningful = canonical(text, tokenize) - NOISE
    if not meaningful and context:
        text = context[:1200]
        meaningful = canonical(text, tokenize) - NOISE
    exact_codes = codes(text)
    entities = meaningful & ALIASES.keys()
    meaningful -= {term for term in meaningful if codes(term)}
    # Exact identifiers/components must survive a pasted ticket's optional words.
    priority = entities | exact_codes | (meaningful & {v for pair in POSITIONS for v in pair})
    meaningful = priority | set(sorted(meaningful - priority)[: max(0, 32 - len(priority))])
    expanded = set(tokenize(text)) - NOISE
    required_terms = set()
    for key in entities:
        required_terms |= alias_terms(tokenize)[key]
    for code in exact_codes:
        letters, number = re.fullmatch(r"([a-z]+)(\d+)", code).groups()
        # Include the legacy split vocabulary for sources written as 'CH 4'.
        required_terms |= {code, letters + "-" + number, letters, number}
    expanded = required_terms | set(
        sorted(expanded - required_terms)[: max(0, 96 - len(required_terms))]
    )
    if len(priority) > 32 or len(required_terms) > 96:
        # Do not silently discard some of an oversized set of required identifiers.
        expanded = set()
    return Query(
        text=text,
        terms=expanded,
        keys=meaningful,
        entities=entities,
        exact_codes=exact_codes,
        procedure=bool(
            re.search(r"\bкак\b|замен|снят|отсоедин|подключ|затяж|инструмент", text, re.I)
        ),
        mapping=bool(re.search(r"\bкод\w*\b|обозначает|соответствует", text, re.I)),
    )


def code_pattern(code):
    letters, number = re.fullmatch(r"([a-z]+)(\d+)", code).groups()
    # Portable PostgreSQL/SQLite regexp boundaries (PostgreSQL's \b differs).
    return rf"(^|[^a-z0-9]){letters}[-– ]?{number}([^a-z0-9]|$)"


def rank(query, title, content, *, kind, trust, tokenize, idf):
    title_keys = canonical(title, tokenize)
    keys = canonical(title + "\n" + content, tokenize)
    keys |= codes(title + "\n" + content)
    if query.exact_codes and not query.exact_codes <= keys:
        return 0.0
    if query.entities and not query.entities <= keys:
        return 0.0
    for pair in POSITIONS:
        requested = query.keys.intersection(pair)
        if len(requested) == 1 and keys.intersection(pair) and not requested <= keys:
            return 0.0
    matches = query.keys & keys
    if not matches or (not query.entities and len(matches) / max(1, len(query.keys)) < 0.5):
        return 0.0
    counts = Counter(tokenize(content))
    length = max(40, sum(counts.values()))
    score = 0.0
    aliases = alias_terms(tokenize)
    for term in matches:
        variants = aliases.get(term, {term})
        tf = min(3, sum(counts.get(word, 0) for word in variants))
        # Saturation and length normalization suppress repeated chat keywords.
        weight = max((idf.get(word, 1.0) for word in variants), default=1.0)
        score += weight * ((tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / 180))) if tf else 0.2)
        if term in title_keys or term in codes(title):
            score += weight * 2.0
    coverage = len(matches) / max(1, len(query.keys))
    score *= 0.5 + coverage
    if kind == "chat":
        score *= 0.45
    if query.procedure and kind == "manual" and trust == "instruction":
        score *= 1.8
    elif query.procedure and kind == "note" and title.startswith("Неполная инструкция:"):
        # Useful evidence, explicitly incomplete and never promoted to instruction trust.
        score *= 1.3
    if query.mapping and kind == "note" and re.search(r"код|справочник|дефект", title, re.I):
        score *= 2.5
    return score


def frequency_weights(frequencies):
    total = max(1, sum(frequencies.values()))
    return {term: 1.0 + math.log(1.0 + total / (count + 1)) for term, count in frequencies.items()}


def excerpt(document, chunk, *, limit=2400, include_start=False):
    """Use complete short procedures; longer ones retain nearby paragraph boundaries."""
    if len(document) <= limit:
        return document
    start = document.find(chunk)
    if start < 0:
        return chunk[:limit]
    left = max(0, start - 350)
    boundary = document.find("\n\n", left, start)
    if boundary >= 0:
        left = boundary + 2
    prefix = "…\n" if left else ""
    if include_start and left > 800:
        head_end = document.rfind("\n\n", 400, 800)
        if head_end < 0:
            head_end = min(800, document.find("\n", 600))
            if head_end < 0:
                head_end = 600
        prefix = document[:head_end] + "\n\n[… часть источника пропущена …]\n\n"
    available = limit - len(prefix) - 2
    right = min(len(document), left + available)
    boundary = document.rfind("\n\n", left + available // 2, right)
    if boundary >= 0:
        right = boundary
    return prefix + document[left:right] + ("\n…" if right < len(document) else "")
