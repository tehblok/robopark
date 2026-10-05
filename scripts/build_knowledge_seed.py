#!/usr/bin/env python3
"""Build a private, redacted JSONL seed for the local knowledge base.

Source files are evidence: their contents are parsed as data and never executed.
The generated directory is intended to stay under the repository's ignored output/.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

MAX_CONTENT_CHARS = 100_000
DEFAULT_CHUNK_CHARS = 12_000
DEFAULT_PART_BYTES = 64 * 1024 * 1024 - 64 * 1024
MAX_SOURCE_BYTES = 768 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED = 64 * 1024 * 1024
MAX_TABLE_ROWS = 20_000
SUPPORTED = {
    ".md",
    ".pdf",
    ".json",
    ".jsonl",
    ".docx",
    ".xlsx",
    ".xlsm",
    ".xls",
    ".csv",
}

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}(?![\w.-])")
PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\w)"
)
SECRET_RE = re.compile(
    r"(?<!\w)(password|passwd|pwd|token|api[_-]?key|secret|authorization|"
    r"пароль|логин|секрет|пин(?:[-_\s]?код)|код\s+доступа)(?!\w)"
    r"(\s*[:=]\s*)([^\s,;]+)",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
URL_AUTH_RE = re.compile(r"(?i)\b(https?://)([^/@\s:]+):([^/@\s]+)@")
URL_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:token|access_token|api[_-]?key|password|secret)=)[^&#\s]+"
)
URL_RE = re.compile(r"(?i)\bhttps?://\S+")
HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_.-]{1,}")
ISSUE_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*-\d+")
ACK_RE = re.compile(
    r"(?i)^\s*(?:ок(?:ей)?|да|нет|готово|сделано|понял[аи]?|принято|спасибо|благодарю)[.! 👍✅]*\s*$"
)
MACHINE_LINE_RE = re.compile(
    r"(?i)^(?:статус(?: изменен)?|тип статуса|sla|id комментария|наблюдатели|links?|relates|subtask)\s*:"
)
SERVICE_CHAT_RE = re.compile(
    r"(?i)(?:поступила новая задача|задачи? с превышением времени|^новые задачи\s*:|"
    r"задача по сливу логов|время отведенное на ремонт|превышено время (?:в очереди|ремонта)|"
    r"^your id\s*:|(?:тикет|блокер)\s+.+\s+закрыт|поменяй название группы|изменил[аи]? название группы)"
)


def stable_id(value: str, length: int = 20) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:length]


def source_id(relative: Path) -> str:
    return f"source:v2:{stable_id(relative.as_posix())}"


def clean_text(value: Any) -> str:
    text = flatten_text(value).replace("\r\n", "\n").replace("\r", "\n")
    text = URL_RE.sub("", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" -\t")
        if (
            not line
            or MACHINE_LINE_RE.match(line)
            or re.search(r"(?i)\bSLA\s*:\s*[{[]", line)
        ):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def clean_tracker_comment(value: Any) -> str:
    text = flatten_text(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(
        r'(?is){%\s*cut\s+["\']Предыдущие\s*сообщения["\']\s*%}.*?{%\s*endcut\s*%}',
        "",
        text,
    )
    kept = []
    skipping_quote = False
    for line in text.splitlines():
        if re.match(r"^\s*>\s*\[В ответ на[^]]*\]", line, re.IGNORECASE):
            skipping_quote = True
            continue
        if skipping_quote and re.match(r"^\s*>", line):
            continue
        skipping_quote = False
        kept.append(line)
    text = "\n".join(kept)
    text = re.sub(r"(?<![\w@])@[A-Za-zА-Яа-я0-9_.-]{2,}", "", text)
    return clean_text(text)


def safe_heading(value: str, fallback: str) -> str:
    value = re.sub(r"[_]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-_—")
    value = re.sub(r"(?i)\b(?:chat ?export|result|dataset)\b", "", value).strip(" .-_—")
    return value[:180] if value else fallback


def manual_heading(first_text: str, path: Path, fallback: str) -> str:
    candidate = safe_heading(re.sub(r"\s*\{#[^}]*\}", "", first_text), "").rstrip(":")
    if (
        not candidate
        or re.fullmatch(
            r"(?i)(?:информация об инструкции|техника безопасности|содержание(?: страницы)?|инструкция)",
            candidate,
        )
        or re.match(r"(?i)^(?:https?://|wiki[./])", candidate)
    ):
        candidate = safe_heading(path.stem, fallback)
    candidate = re.sub(r"(?i)\s+(?:Вики|Ви)$", "", candidate)
    return (
        fallback if re.match(r"(?i)^(?:https?://|wiki[./])", candidate) else candidate
    )


def select_manual_title(texts: Iterable[str], path: Path, fallback: str) -> str:
    lines = [
        clean_text(line)
        for text in texts
        for line in text.splitlines()
        if clean_text(line)
    ]
    candidate = manual_heading(lines[0] if lines else "", path, fallback)
    filename_title = safe_heading(re.sub(r"\s+\(\d+\)$", "", path.stem), fallback)
    operation = re.compile(
        r"(?i)^(?:замена|снятие|установка|регулировка|ремонт|демонтаж|монтаж|сборка|разборка)\b"
    )
    alternatives = [
        line
        for line in lines
        if operation.match(line)
        and 2 <= len(line.split()) <= 12
        and len(line) <= 140
        and not line.endswith((".", ";", ":", "!", "?"))
    ]
    joined = "\n".join(lines)
    descriptive = re.search(
        r"(?i)(инструкция по (?:ремонту|установке|замене|снятию|монтажу|демонтажу).+?)(?=Необходим|Пошагов|\n|$)",
        joined,
    )
    embedded = re.search(r"(?im)^инструкция\s*[|:—-]\s*(.{3,140})$", joined)
    spaced_letters = bool(re.search(r"(?:\b[\wА-Яа-я]\s+){4,}", candidate))
    digit_density = sum(character.isdigit() for character in candidate) / max(
        len(candidate), 1
    )
    generic = bool(
        re.fullmatch(r"(?i)(?:документ|инструкция)\s+[a-f0-9]{8}", candidate)
    )
    truncated = bool(re.search(r"(?i)\s(?:ви|м)$", candidate))
    generic_section = bool(
        re.match(r"(?i)^(?:материалы|необходимые инструменты)", candidate)
    )
    if operation.match(candidate):
        return candidate
    if alternatives and (
        generic
        or truncated
        or spaced_letters
        or len(candidate) > 140
        or digit_density > 0.25
        or not operation.match(candidate)
    ):
        return alternatives[-1]
    if descriptive:
        descriptive_title = clean_text(descriptive.group(1)).rstrip(" .:;")
        if len(descriptive_title.split()) >= 4:
            return descriptive_title
    if embedded:
        embedded_title = clean_text(embedded.group(1)).rstrip(" .:;")
        if re.match(r"^[A-Za-zА-Яа-я]", embedded_title):
            return embedded_title
    if generic_section or spaced_letters:
        return filename_title
    return candidate


def clean_manual_text(text: str) -> str:
    kept = []
    for raw_line in text.replace("\r", "\n").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if re.search(r"(?i)^автор инструкции\s*:", line):
            continue
        if re.search(r"(?i)^обновлено\s+\d|^\d{2}\.\d{2}\.\d{4}.*\bwiki\b", line):
            continue
        if re.search(r"(?i)^о сервисе\b.*\bсообщество\b", line):
            continue
        if re.search(r"(?i)^содержание страницы$|^/sdc\s+/robot\s+/", line):
            continue
        if re.match(r"(?i)^https?://\S+(?:\s+\d+/\d+)?$", line):
            continue
        line = URL_RE.sub("", line).strip()
        line = re.sub(r"(?i)\s+обновлено\s+\d{1,2}\s+\S+\s+\d{4}.*$", "", line).strip()
        if line:
            kept.append(line)
    return clean_text("\n".join(kept))


def render_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"\s+", " ", str(value)).strip()


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            flatten_text(item.get("text", "") if isinstance(item, dict) else item)
            for item in value
        )
    if isinstance(value, dict):
        return flatten_text(value.get("text", value.get("body", "")))
    return "" if value is None else str(value)


class Redactor:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def redact(self, text: str, names: Iterable[str] = ()) -> str:
        def replace(
            pattern: re.Pattern[str], replacement: str, key: str, value: str
        ) -> str:
            def repl(_: re.Match[str]) -> str:
                self.counts[key] += 1
                return replacement

            return pattern.sub(repl, value)

        text = replace(
            URL_AUTH_RE, r"\1[учётные данные удалены]@", "url_credentials", text
        )
        text = replace(
            URL_SECRET_QUERY_RE, r"\1[секрет удалён]", "url_credentials", text
        )
        text = replace(BEARER_RE, "Bearer [секрет удалён]", "secret", text)

        def secret(match: re.Match[str]) -> str:
            self.counts["secret"] += 1
            return f"{match.group(1)}{match.group(2)}[секрет удалён]"

        text = SECRET_RE.sub(secret, text)
        text = replace(EMAIL_RE, "[email удалён]", "email", text)
        text = replace(PHONE_RE, "[телефон удалён]", "phone", text)
        text = replace(HANDLE_RE, "[упоминание удалено]", "handle", text)
        for name in sorted(
            {n.strip() for n in names if isinstance(n, str) and len(n.strip()) >= 2},
            key=len,
            reverse=True,
        ):
            pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
            text, hits = pattern.subn("[имя удалено]", text)
            if hits:
                self.counts["name"] += hits
        return text


def chunks(text: str, limit: int) -> Iterator[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    while text:
        if len(text) <= limit:
            yield text
            return
        cut = max(
            text.rfind("\n\n", 0, limit + 1),
            text.rfind("\n", 0, limit + 1),
            text.rfind(" ", 0, limit + 1),
        )
        if cut < max(40, limit // 3):
            cut = limit
        piece, text = text[:cut].strip(), text[cut:].strip()
        if piece:
            yield piece


class SeedWriter:
    def __init__(self, output: Path, part_max_bytes: int) -> None:
        self.output = output
        self.part_max_bytes = part_max_bytes
        self.parts: list[dict[str, Any]] = []
        self.handle = None
        self.path: Path | None = None
        self.size = 0
        self.count = 0
        self.total = 0

    def _open(self) -> None:
        index = len(self.parts) + 1
        name = "seed.jsonl" if index == 1 else f"part-{index:04d}.jsonl"
        self.path = self.output / name
        self.handle = self.path.open("wb")
        self.size = self.count = 0

    def _close(self) -> None:
        if self.handle is None or self.path is None:
            return
        self.handle.flush()
        self.handle.close()
        payload = self.path.read_bytes()
        self.parts.append(
            {
                "path": self.path.name,
                "documents": self.count,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
        self.handle = None

    def write(self, document: dict[str, str]) -> None:
        line = (
            json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        if len(line) > self.part_max_bytes:
            raise ValueError("part-max-bytes is too small for one document")
        if self.handle is None:
            self._open()
        if self.size and self.size + len(line) > self.part_max_bytes:
            self._close()
            self._open()
        assert self.handle is not None
        self.handle.write(line)
        self.size += len(line)
        self.count += 1
        self.total += 1

    def finish(self) -> None:
        if self.handle is None:
            self._open()
        self._close()


def iter_array(path: Path, key: str | None = None) -> Iterator[Any]:
    """Incrementally decode a top-level array or a named top-level array."""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        buffer = ""
        start = False
        pattern = (
            re.compile(rf'"{re.escape(key)}"\s*:\s*\[')
            if key
            else re.compile(r"^\s*\[")
        )
        while not start:
            piece = stream.read(1024 * 1024)
            if not piece:
                return
            buffer += piece
            match = pattern.search(buffer)
            if match:
                buffer = buffer[match.end() :]
                start = True
            elif len(buffer) > 4 * 1024 * 1024:
                raise ValueError(
                    f"array key {key!r} was not found near the document start"
                )
        eof = False
        while True:
            buffer = buffer.lstrip()
            if buffer.startswith(","):
                buffer = buffer[1:].lstrip()
            if buffer.startswith("]"):
                return
            try:
                value, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                piece = stream.read(1024 * 1024)
                if not piece:
                    if buffer.strip() and not eof:
                        raise
                    return
                buffer += piece
                continue
            yield value
            buffer = buffer[end:]


def json_shape(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        prefix = stream.read(2 * 1024 * 1024)
    if re.search(r'"messages"\s*:\s*\[', prefix):
        return "telegram"
    if re.search(r'"tickets"\s*:\s*\[', prefix):
        return "tickets"
    if prefix.lstrip().startswith("["):
        return "list"
    return "object"


class Builder:
    def __init__(
        self,
        root: Path,
        output: Path,
        chunk_chars: int,
        part_max_bytes: int,
        *,
        excluded_directories: tuple[Path, ...] = (),
    ) -> None:
        self.root = root
        self.output = output
        self.chunk_chars = min(chunk_chars, MAX_CONTENT_CHARS)
        self.writer = SeedWriter(output, part_max_bytes)
        self.redactor = Redactor()
        self.coverage: dict[str, Counter[str]] = defaultdict(Counter)
        self.skipped: Counter[str] = Counter()
        self.kinds: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.quarantined: Counter[str] = Counter()
        self.flagged: Counter[str] = Counter()
        self.unavailable_attachments = 0
        self.canonical_tickets: dict[str, str] = {}
        self.document_fingerprints: dict[str, str] = {}
        self.source_map: dict[str, dict[str, Any]] = {}
        self.excluded_directories = {path.resolve() for path in excluded_directories}
        if root.resolve() in self.excluded_directories or any(
            not path.is_relative_to(root.resolve())
            for path in self.excluded_directories
        ):
            raise ValueError("excluded_directory_outside_input")

    def add(
        self, relative: Path, title: str, content: str, kind: str, suffix: str = ""
    ) -> None:
        content = self.redactor.redact(content).strip()
        if not content:
            self.skipped["empty_content"] += 1
            self.disposition(relative, "skipped", "empty_content")
            return
        public_title = self.redactor.redact(title).strip()[:240]
        semantic_title = re.sub(r"\s+", " ", public_title).casefold()
        fingerprint = hashlib.sha256(
            "\0".join(
                (kind, semantic_title, re.sub(r"\s+", " ", content).casefold())
            ).encode()
        ).hexdigest()
        if fingerprint in self.document_fingerprints:
            self.skipped["duplicate_content"] += 1
            self.disposition(
                relative,
                "skipped",
                "duplicate_content",
                canonical_duplicate_ref=self.document_fingerprints[fingerprint],
            )
            return
        canonical_ref = source_id(relative) + suffix
        self.document_fingerprints[fingerprint] = canonical_ref
        kind_label = {
            "manual": "инструкция",
            "note": "справочный материал",
            "ticket": "ремонтный тикет",
            "chat": "рабочая переписка",
        }.get(kind, kind)
        context_prefix = f"Контекст документа: {kind_label}\nНазвание: "
        title_limit = max(0, self.chunk_chars - len(context_prefix) - 4)
        context = f"{context_prefix}{public_title[:title_limit]}"
        pieces = list(chunks(content, max(1, self.chunk_chars - len(context) - 2)))
        for number, piece in enumerate(pieces, 1):
            ref = source_id(relative) + suffix
            if len(pieces) > 1:
                ref += f"#part-{number:04d}"
            rendered_content = f"{context}\n\n{piece}"
            self.writer.write(
                {
                    "title": public_title,
                    "content": rendered_content,
                    "kind": kind,
                    "source_ref": ref,
                }
            )
            self.kinds[kind] += 1
            self.source_map.setdefault(
                source_id(relative),
                {"relative_path": relative.as_posix(), "evidence": []},
            )["evidence"].append({"status": "included", "source_ref": ref})

    def register_source(self, relative: Path) -> None:
        identifier = source_id(relative)
        self.source_map.setdefault(
            identifier, {"relative_path": relative.as_posix(), "evidence": []}
        )

    def disposition(
        self,
        relative: Path,
        status: str,
        reason: str,
        *,
        detail: str = "",
        canonical_duplicate_ref: str = "",
    ) -> None:
        entry = self.source_map.setdefault(
            source_id(relative), {"relative_path": relative.as_posix(), "evidence": []}
        )
        event = {"status": status, "reason": reason}
        if detail:
            event["detail"] = detail[:300]
        if canonical_duplicate_ref:
            event["canonical_duplicate_ref"] = canonical_duplicate_ref
        entry["evidence"].append(event)

    def quarantine(self, relative: Path, reason: str, evidence: str = "") -> None:
        self.quarantined[reason] += 1
        self.disposition(relative, "quarantined", reason, detail=evidence)

    def flag(self, relative: Path, reason: str, evidence: str = "") -> None:
        self.flagged[reason] += 1
        self.disposition(relative, "flagged", reason, detail=evidence)

    def build(self) -> dict[str, Any]:
        # The ticket export includes the same repairs in dataset.jsonl and a
        # verbose Markdown rendering. Prefer structured, identity-redacted
        # records over repeated metadata/changelog dumps.
        for path, prepass_relative in self.walk(count_skipped=False):
            if path.name != "dataset.jsonl" or path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if (
                        isinstance(item, dict)
                        and {"key", "issue", "comments"} <= item.keys()
                        and isinstance(item["key"], str)
                    ):
                        ticket_key = item["key"]
                        self.canonical_tickets[ticket_key] = (
                            source_id(prepass_relative)
                            + f"#ticket-{stable_id(ticket_key, 12)}"
                        )
        for path, relative in self.walk():
            extension = path.suffix.lower()
            self.register_source(relative)
            self.coverage[extension]["discovered"] += 1
            if extension not in SUPPORTED:
                self.skipped["unsupported_format"] += 1
                self.coverage[extension]["skipped"] += 1
                self.disposition(relative, "skipped", "unsupported_format")
                continue
            if self.is_structural_index(path, relative):
                self.skipped["structural_index"] += 1
                self.coverage[extension]["skipped"] += 1
                self.disposition(relative, "skipped", "structural_index")
                continue
            if extension == ".md" and path.stem in self.canonical_tickets:
                self.skipped["duplicate_ticket_rendering"] += 1
                self.coverage[extension]["skipped"] += 1
                self.disposition(
                    relative,
                    "skipped",
                    "duplicate_ticket_rendering",
                    canonical_duplicate_ref=self.canonical_tickets[path.stem],
                )
                continue
            try:
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    raise ValueError("source_too_large")
                getattr(self, f"read_{extension[1:]}")(path, relative)
                if not self.source_map[source_id(relative)]["evidence"]:
                    self.skipped["no_usable_content"] += 1
                    self.disposition(relative, "skipped", "no_usable_content")
                self.coverage[extension]["processed"] += 1
            # A corrupt private source must not abort the rest of a multi-GB
            # corpus; the bounded reader records a generic reason instead.
            except Exception as error:  # noqa: BLE001
                reason = (
                    str(error)
                    if str(error)
                    in {
                        "source_too_large",
                        "encrypted_pdf",
                        "pdf_reader_unavailable",
                        "xls_reader_unavailable",
                    }
                    else "parse_error"
                )
                self.errors[reason] += 1
                self.coverage[extension]["skipped"] += 1
                self.disposition(relative, "error", reason)
        self.writer.finish()
        return {
            "schema": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_fingerprint": self.fingerprint(),
            "documents": self.writer.total,
            "parts": self.writer.parts,
            "documents_by_kind": dict(sorted(self.kinds.items())),
            "coverage": {
                key: dict(value) for key, value in sorted(self.coverage.items())
            },
            "skipped_by_reason": dict(sorted(self.skipped.items())),
            "quarantined_by_reason": dict(sorted(self.quarantined.items())),
            "flagged_by_reason": dict(sorted(self.flagged.items())),
            "unavailable_attachments": self.unavailable_attachments,
            "redactions": dict(sorted(self.redactor.counts.items())),
            "errors": dict(sorted(self.errors.items())),
        }

    def is_structural_index(self, path: Path, relative: Path) -> bool:
        if path.stem.casefold() not in {"index", "manifest", "files"}:
            return False
        if "park_blockers" in relative.as_posix().casefold():
            return True
        if path.suffix.lower() == ".csv":
            prefix = path.read_text(encoding="utf-8", errors="replace")[
                :4096
            ].casefold()
            return "path" in prefix and ("size" in prefix or "sha" in prefix)
        return "park_blockers" in relative.as_posix().casefold()

    def walk(self, *, count_skipped: bool = True) -> Iterator[tuple[Path, Path]]:
        stack = [self.root]
        while stack:
            directory = stack.pop()
            with os.scandir(directory) as entries:
                for entry in sorted(entries, key=lambda item: item.name, reverse=True):
                    path = Path(entry.path)
                    if entry.is_symlink():
                        if count_skipped:
                            self.skipped["symlink"] += 1
                            relative = path.relative_to(self.root)
                            self.register_source(relative)
                            self.disposition(relative, "skipped", "symlink")
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if path.resolve() in self.excluded_directories:
                            continue
                        stack.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        relative = path.relative_to(self.root)
                        if (self.root / relative).resolve().is_relative_to(self.root):
                            yield path, relative
                        else:
                            self.skipped["outside_input"] += 1

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for directory, names, filenames in os.walk(self.root, followlinks=False):
            names[:] = sorted(
                name
                for name in names
                if not (Path(directory) / name).is_symlink()
                and (Path(directory) / name).resolve() not in self.excluded_directories
            )
            for name in sorted(filenames):
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                relative = path.relative_to(self.root)
                digest.update(relative.as_posix().encode())
                digest.update(b"\0")
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
        return digest.hexdigest()

    def read_md(self, path: Path, relative: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        heading = next(
            (
                line.lstrip("# ").strip()
                for line in text.splitlines()
                if line.startswith("#") and line.lstrip("# ").strip()
            ),
            "",
        )
        body = clean_text(text)
        if ISSUE_KEY_RE.fullmatch(body) or (
            ISSUE_KEY_RE.fullmatch(path.stem) and body in {path.stem, heading}
        ):
            self.skipped["bare_issue_key"] += 1
            self.quarantine(relative, "insufficient_ticket_evidence", body)
            return
        kind = "ticket" if ISSUE_KEY_RE.fullmatch(path.stem) else "note"
        # File format does not establish authority. Markdown exports and AI
        # skills remain experience/notes; only dedicated repair manuals carry
        # the manual kind. Administrator decides activation during import.
        self.add(
            relative,
            safe_heading(heading, f"Материал {stable_id(relative.as_posix(), 8)}"),
            body,
            kind,
        )

    def read_docx(self, path: Path, relative: Path) -> None:
        with zipfile.ZipFile(path) as archive:
            if (
                sum(item.file_size for item in archive.infolist())
                > MAX_DOCX_UNCOMPRESSED
            ):
                raise ValueError("source_too_large")
            xml = archive.read("word/document.xml")
            visual_count = sum(
                name.startswith("word/media/") for name in archive.namelist()
            )
        root = ElementTree.fromstring(xml)
        paragraphs = []
        for paragraph in root.iter(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
        ):
            value = "".join(
                node.text or ""
                for node in paragraph.iter(
                    "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
                )
            )
            value = clean_manual_text(value)
            if value:
                paragraphs.append(value)
        if not paragraphs:
            title = safe_heading(
                path.stem, f"Документ {stable_id(relative.as_posix(), 8)}"
            )
            self.flag(relative, "image_only_document", f"embedded_media={visual_count}")
            self.add(
                relative,
                f"Неполная инструкция: {title}",
                "[Текст не извлечён: исходный документ состоит из визуальных материалов. Для ремонта необходимо открыть оригинал.]",
                "note",
                "#section-0001",
            )
            return
        first = select_manual_title(
            paragraphs, path, f"Документ {stable_id(relative.as_posix(), 8)}"
        )
        sample = " ".join(paragraphs[:8]).casefold()
        catalog = bool(
            re.search(
                r"\b(?:каталог|артикул|номенклатур|перечень|справочник|склад)\w*",
                sample,
            )
            or re.match(r"(?i)^(?:детали|parts?)\b", path.stem)
        )
        kind = "note" if catalog else "manual"
        prefix = "Справочник" if catalog else "Инструкция"
        content = "\n\n".join(paragraphs)
        if visual_count:
            content += "\n\n[В исходном документе есть визуальные материалы; расположение деталей необходимо сверить с оригиналом.]"
            self.flag(
                relative, "visual_context_present", f"embedded_media={visual_count}"
            )
        self.add(relative, f"{prefix}: {first}", content, kind, "#section-0001")

    def read_pdf(self, path: Path, relative: Path) -> None:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ValueError("pdf_reader_unavailable") from error
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf")
        pages = [
            (number, clean_manual_text(page.extract_text() or ""))
            for number, page in enumerate(reader.pages, 1)
        ]
        usable = [(number, text) for number, text in pages if len(text) >= 40]
        missing = len(pages) - len(usable)
        if not usable:
            title = safe_heading(
                path.stem, f"Инструкция {stable_id(relative.as_posix(), 8)}"
            )
            self.flag(relative, "image_only_document", f"pages={len(pages)}")
            self.add(
                relative,
                f"Неполная инструкция: {title}",
                "[Текст не извлечён ни с одной страницы; визуальные материалы и шаги необходимо сверить с оригиналом.]",
                "note",
                f"#pages-{1:04d}-{len(pages):04d}",
            )
            return
        title = select_manual_title(
            (text for _, text in usable),
            path,
            f"Инструкция {stable_id(relative.as_posix(), 8)}",
        )
        # Keep adjacent procedure steps together. The normal content chunker
        # splits only if the full extracted manual exceeds the API limit.
        start, end = usable[0][0], usable[-1][0]
        extracted = dict(usable)
        content = "\n\n".join(
            f"[Страница {number}]\n{extracted[number]}"
            if number in extracted
            else f"[Страница {number}: текст не извлечён; визуальные материалы необходимо сверить с оригиналом.]"
            for number in range(1, len(pages) + 1)
        )
        if missing:
            self.flag(
                relative, "incomplete_visual_document", f"pages_without_text={missing}"
            )
        self.add(
            relative,
            f"{'Неполная инструкция' if missing else 'Инструкция'}: {title}",
            content,
            "note" if missing else "manual",
            f"#pages-{start:04d}-{end:04d}",
        )

    def read_json(self, path: Path, relative: Path) -> None:
        shape = json_shape(path)
        if shape == "telegram":
            self.read_telegram(path, relative)
        elif shape == "tickets":
            for ticket in iter_array(path, "tickets"):
                self.add_ticket(relative, ticket)
        elif shape == "list":
            for index, item in enumerate(iter_array(path), 1):
                if isinstance(item, dict) and (
                    {"summary", "description", "comments", "key"} & set(item)
                ):
                    self.add_ticket(relative, item, index)
                else:
                    self.add_generic_json(relative, item, index)
        else:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict) and "sections" in data and "key" in data:
                # Full-year Tracker exports wrap each API result in an explicit
                # success envelope. Never flatten failed sections as knowledge.
                sections = data.get("sections")
                issue_section = (
                    sections.get("issue", {}) if isinstance(sections, dict) else {}
                )
                issue = (
                    issue_section.get("value")
                    if isinstance(issue_section, dict)
                    else None
                )
                if (
                    not isinstance(issue_section, dict)
                    or issue_section.get("ok") is not True
                    or not isinstance(issue, dict)
                ):
                    self.quarantine(relative, "tracker_issue_unavailable")
                    return
                ticket = {"key": data["key"], "issue": issue}
                for section in ("comments", "attachments"):
                    envelope = sections.get(section, {})
                    if isinstance(envelope, dict) and envelope.get("ok") is True:
                        ticket[section] = envelope.get("value", [])
                    else:
                        self.flag(relative, "tracker_section_unavailable", section)
                self.add_ticket(relative, ticket)
            else:
                self.add_generic_json(relative, data, 1)

    def read_jsonl(self, path: Path, relative: Path) -> None:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for index, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict) and (
                    {"summary", "description", "comments", "key"} & set(item)
                ):
                    self.add_ticket(relative, item, index)
                else:
                    self.add_generic_json(relative, item, index)

    def read_csv(self, path: Path, relative: Path) -> None:
        with path.open(
            "r", encoding="utf-8-sig", errors="replace", newline=""
        ) as stream:
            sample = stream.read(8192)
            stream.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = list(
                itertools.islice(csv.reader(stream, dialect), MAX_TABLE_ROWS + 2)
            )
        if len(rows) > MAX_TABLE_ROWS + 1:
            self.flag(relative, "table_truncated", f"limit={MAX_TABLE_ROWS}")
            rows = rows[: MAX_TABLE_ROWS + 1]
        self.add_table(relative, safe_heading(path.stem, "Таблица"), rows)

    def read_xlsx(self, path: Path, relative: Path) -> None:
        self._read_ooxml_workbook(path, relative)

    def read_xlsm(self, path: Path, relative: Path) -> None:
        # XLSM is an OOXML container. Only cell XML is read; VBA streams are
        # never loaded or executed.
        self._read_ooxml_workbook(path, relative)

    def read_xls(self, path: Path, relative: Path) -> None:
        try:
            import xlrd
        except ImportError as error:
            self.quarantine(relative, "xls_reader_unavailable")
            raise ValueError("xls_reader_unavailable") from error
        book = xlrd.open_workbook(path, on_demand=True)
        try:
            for sheet_number, sheet in enumerate(book.sheets(), 1):
                rows = [
                    [sheet.cell_value(row, column) for column in range(sheet.ncols)]
                    for row in range(sheet.nrows)
                ]
                self.add_table(
                    relative,
                    safe_heading(sheet.name, safe_heading(path.stem, "Таблица")),
                    rows,
                    f"#sheet-{sheet_number:04d}",
                )
        finally:
            book.release_resources()

    def _read_ooxml_workbook(self, path: Path, relative: Path) -> None:
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        rel_namespace = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        document_rel = (
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        )
        with zipfile.ZipFile(path) as archive:
            if (
                sum(item.file_size for item in archive.infolist())
                > MAX_DOCX_UNCOMPRESSED * 4
            ):
                raise ValueError("source_too_large")
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ElementTree.fromstring(
                    archive.read("xl/sharedStrings.xml")
                )
                for item in shared_root.iter(namespace + "si"):
                    shared.append(
                        "".join(node.text or "" for node in item.iter(namespace + "t"))
                    )
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships = ElementTree.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            targets = {
                item.attrib["Id"]: item.attrib["Target"].lstrip("/")
                for item in relationships.iter(rel_namespace + "Relationship")
            }
            for sheet_number, sheet in enumerate(workbook.iter(namespace + "sheet"), 1):
                target = targets.get(sheet.attrib.get(document_rel + "id", ""), "")
                target = target if target.startswith("xl/") else "xl/" + target
                if target not in archive.namelist():
                    continue
                sheet_root = ElementTree.fromstring(archive.read(target))
                merged: dict[str, str] = {}
                for merge in sheet_root.iter(namespace + "mergeCell"):
                    reference = merge.attrib.get("ref", "")
                    if ":" in reference:
                        merged[reference.split(":", 1)[1]] = reference.split(":", 1)[0]
                values: dict[str, str] = {}
                max_col = 0
                row_values: dict[int, dict[int, str]] = {}
                for row in sheet_root.iter(namespace + "row"):
                    current: dict[int, str] = {}
                    for cell in row.iter(namespace + "c"):
                        reference = cell.attrib.get("r", "")
                        letters = re.match(r"[A-Z]+", reference)
                        if not letters:
                            continue
                        column = 0
                        for letter in letters.group(0):
                            column = column * 26 + ord(letter) - 64
                        max_col = max(max_col, column)
                        formula = cell.find(namespace + "f")
                        if formula is not None:
                            value = "[формула не вычислялась]"
                        elif cell.attrib.get("t") == "inlineStr":
                            value = "".join(
                                node.text or "" for node in cell.iter(namespace + "t")
                            )
                        else:
                            value_node = cell.find(namespace + "v")
                            value = (
                                value_node.text
                                if value_node is not None
                                and value_node.text is not None
                                else ""
                            )
                            if (
                                cell.attrib.get("t") == "s"
                                and value.isdigit()
                                and int(value) < len(shared)
                            ):
                                value = shared[int(value)]
                        current[column] = value
                        values[reference] = value
                    if current:
                        row_number = int(row.attrib.get("r", len(row_values) + 1))
                        row_values[row_number] = current
                # Fill merged cells with the anchor value so mappings retain
                # group/header context without interpreting spreadsheet logic.
                for bottom_right, anchor in merged.items():
                    if anchor not in values:
                        continue
                    start = re.match(r"([A-Z]+)(\d+)", anchor)
                    end = re.match(r"([A-Z]+)(\d+)", bottom_right)
                    if not start or not end:
                        continue

                    def column_number(letters: str) -> int:
                        result = 0
                        for letter in letters:
                            result = result * 26 + ord(letter) - 64
                        return result

                    for row_index in range(int(start.group(2)), int(end.group(2)) + 1):
                        current = row_values.setdefault(row_index, {})
                        for column in range(
                            column_number(start.group(1)),
                            column_number(end.group(1)) + 1,
                        ):
                            current.setdefault(column, values[anchor])
                rows = [
                    [
                        row_values.get(row, {}).get(column, "")
                        for column in range(1, max_col + 1)
                    ]
                    for row in range(1, max(row_values, default=0) + 1)
                ]
                name = safe_heading(
                    sheet.attrib.get("name", ""), safe_heading(path.stem, "Таблица")
                )
                self.add_table(relative, name, rows, f"#sheet-{sheet_number:04d}")

    def add_table(
        self,
        relative: Path,
        sheet_name: str,
        rows: list[list[Any]],
        suffix: str = "#sheet-0001",
    ) -> None:
        rows = [[render_cell(cell) for cell in row] for row in rows[:20_001]]
        rows = [row for row in rows if any(cell for cell in row)]
        if not rows:
            self.quarantine(relative, "empty_table", sheet_name)
            return
        header_index = next(
            (
                index
                for index, row in enumerate(rows[:20])
                if sum(bool(cell) for cell in row) >= 2
            ),
            0,
        )
        headers = [
            cell or f"Поле {index + 1}" for index, cell in enumerate(rows[header_index])
        ]
        emitted = 0
        for row_number, row in enumerate(rows[header_index + 1 :], header_index + 2):
            pairs = []
            for index, value in enumerate(row):
                if not value:
                    continue
                header = headers[index] if index < len(headers) else f"Поле {index + 1}"
                if re.search(
                    r"(?i)^(?:поле\s*\d+|id|.*\b(?:owner|uid|url)\b.*)$",
                    header.strip(),
                ):
                    self.skipped["administrative_table_column"] += 1
                    continue
                pairs.append(f"{header}: {value}")
            content = "\n".join(pairs)
            if len(pairs) < 2 or len(re.sub(r"\W", "", content)) < 8:
                self.skipped["administrative_or_empty_row"] += 1
                continue
            code = next(
                (
                    value
                    for header, value in zip(headers, row)
                    if "код" in header.casefold() and value
                ),
                "",
            )
            meaning = next(
                (
                    value
                    for header, value in zip(headers, row)
                    if any(
                        word in header.casefold()
                        for word in ("расшиф", "ошиб", "дефект", "опис")
                    )
                    and value
                ),
                "",
            )
            detail = " — ".join(part for part in (code, meaning) if part)
            title = f"Справочник: {sheet_name}" + (
                f" — {detail[:100]}" if detail else f" — строка {row_number}"
            )
            self.add(relative, title, content, "note", f"{suffix}-row-{row_number:06d}")
            emitted += 1
        if not emitted:
            self.quarantine(relative, "table_without_useful_rows", sheet_name)

    def read_telegram(self, path: Path, relative: Path) -> None:
        names: set[str] = set()
        parent: dict[Any, Any] = {}
        raw_messages: list[dict[str, Any]] = []
        for message in iter_array(path, "messages"):
            if not isinstance(message, dict):
                continue
            raw_messages.append(message)
            for key in ("from", "actor", "author", "from_name"):
                if isinstance(message.get(key), str):
                    names.add(message[key])
            identifier = message.get("id")
            if identifier is not None:
                parent[identifier] = message.get("reply_to_message_id")

        def root_of(identifier: Any) -> Any:
            seen = set()
            while parent.get(identifier) is not None and identifier not in seen:
                seen.add(identifier)
                identifier = parent[identifier]
            return identifier

        branches: dict[Any, list[tuple[datetime | None, str]]] = defaultdict(list)
        for message in raw_messages:
            if (
                not isinstance(message, dict)
                or message.get("type", "message") != "message"
            ):
                continue
            text = flatten_text(message.get("text", "")).strip()
            if not text:
                continue
            file_ref = message.get("file") or message.get("photo")
            if isinstance(file_ref, str):
                attachment = (path.parent / file_ref).resolve()
                if not attachment.is_relative_to(self.root) or not attachment.is_file():
                    self.unavailable_attachments += 1
            text = clean_text(text)
            if not text or SERVICE_CHAT_RE.search(text):
                self.skipped["chat_service_message"] += 1
                continue
            text = self.redactor.redact(text, names)
            if ACK_RE.fullmatch(text):
                self.skipped["chat_acknowledgement"] += 1
                continue
            timestamp = None
            if isinstance(message.get("date"), str):
                try:
                    timestamp = datetime.fromisoformat(
                        message["date"].replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
            root = root_of(message.get("id"))
            branches[root].append((timestamp, text))
        for root, messages in branches.items():
            cases: list[list[tuple[datetime | None, str]]] = []
            for timestamp, text in messages:
                previous = cases[-1][-1][0] if cases else None
                time_gap = (
                    previous is not None
                    and timestamp is not None
                    and abs((timestamp - previous).total_seconds()) > 6 * 3600
                )
                robots = set(re.findall(r"(?i)\b[a-z]\d{3,}\b", text))
                current_robots = (
                    set(
                        re.findall(
                            r"(?i)\b[a-z]\d{3,}\b",
                            " ".join(value for _, value in cases[-1]),
                        )
                    )
                    if cases
                    else set()
                )
                robot_changed = bool(
                    robots and current_robots and not robots <= current_robots
                )
                if not cases or time_gap or robot_changed or len(cases[-1]) >= 16:
                    cases.append([])
                cases[-1].append((timestamp, text))
            for case_number, case in enumerate(cases, 1):
                texts = [text for _, text in case]
                if len(texts) == 1 and (
                    len(texts[0]) < 160
                    or not re.search(
                        r"(?i)\b(?:проверь|замен|установ|измер|отключ|подключ|очист|диагност)\w*",
                        texts[0],
                    )
                ):
                    self.quarantine(
                        relative,
                        "isolated_chat_without_answer",
                        f"root={stable_id(str(root), 8)}",
                    )
                    continue
                if not any(len(re.sub(r"\W", "", value)) >= 12 for value in texts):
                    self.quarantine(
                        relative,
                        "chat_without_repair_context",
                        f"root={stable_id(str(root), 8)}",
                    )
                    continue
                topic = safe_heading(texts[0], "рабочий вопрос")[:90]
                branch_hash = stable_id(
                    f"{relative.as_posix()}:{root}:{case_number}", 12
                )
                self.add(
                    relative,
                    f"Случай из переписки: {topic}",
                    "\n\n".join(texts),
                    "chat",
                    f"#case-{branch_hash}",
                )

    def add_ticket(self, relative: Path, ticket: Any, index: int = 0) -> None:
        if not isinstance(ticket, dict):
            return
        names = set()
        comments = ticket.get("comments", [])
        issue = ticket.get("issue") if isinstance(ticket.get("issue"), dict) else {}

        def identity_values(value: Any) -> Iterator[str]:
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for key in ("display", "display_name", "name", "login", "username"):
                    if isinstance(value.get(key), str):
                        yield value[key]

        for key in ("assignee", "createdBy", "updatedBy", "resolvedBy"):
            names.update(identity_values(ticket.get(key)))
            names.update(identity_values(issue.get(key)))
        if isinstance(comments, list):
            for comment in comments:
                if isinstance(comment, dict):
                    for key in (
                        "author",
                        "from",
                        "name",
                        "display_name",
                        "createdBy",
                        "updatedBy",
                    ):
                        names.update(identity_values(comment.get(key)))
        if isinstance(ticket.get("local_fields"), dict):
            for name, value in ticket["local_fields"].items():
                if name.endswith("reporterRC"):
                    names.update(identity_values(value))
        raw_summary = clean_text(
            ticket.get("summary") or ticket.get("title") or issue.get("summary")
        )
        summary_match = re.search(r"(?i)\bby\s+([\w.-]{3,})\b", raw_summary)
        if summary_match:
            names.add(summary_match.group(1))
        key = clean_text(ticket.get("key", ""))
        administrative_summary = bool(
            re.fullmatch(r"(?i)\s*\[[a-z]+\d+\].*\bby\s+[\w.-]+\s*", raw_summary)
        )
        summary = self.redactor.redact(raw_summary, names)

        def useful_description(value: Any) -> str:
            text = clean_text(value)
            kept = []
            for line in text.splitlines():
                stripped = re.sub(r"[*_`]+", "", line).strip()
                if re.match(r"^(?:<\[[^]]*\]>|<\{Файлы|\}>|/iframe/\().*$", stripped):
                    continue
                if re.match(
                    r"(?i)^(?:suf|priority|приоритет|шаблон|zone|port|partner|time|rover name|mode|reported mode|branch|reporter|orders count|order status|point start|point end|rover coordinates|links?)\s*[:–—-]",
                    stripped,
                ):
                    continue
                stripped = re.sub(
                    r"(?i)^(?:comment|classificator|рекомендации|что было сделано)\s*:\s*",
                    "",
                    stripped,
                )
                if stripped:
                    kept.append(stripped)
            return "\n".join(kept)

        symptom = useful_description(
            ticket.get("description") or issue.get("description")
        )

        def displays(value: Any) -> list[str]:
            if not isinstance(value, list):
                value = [value]
            result = []
            for item in value:
                if isinstance(item, dict):
                    item = item.get("display") or item.get("name") or item.get("key")
                rendered = clean_text(item)
                if rendered:
                    result.append(rendered)
            return result

        components = displays(
            ticket.get("components")
            or ticket.get("component")
            or issue.get("components")
        )
        defect = (
            ticket.get("defect_code")
            or ticket.get("defect")
            or issue.get("theDefectCode")
        )
        if defect is None:
            defect = next(
                (
                    value
                    for name, value in issue.items()
                    if name.endswith("--theDefectCode")
                ),
                None,
            )
        if defect is None and isinstance(ticket.get("local_fields"), dict):
            defect = ticket["local_fields"].get("theDefectCode")
        defect_text = clean_text(defect)
        if defect_text.casefold() in {"n/a", "none", "null", "-"}:
            defect_text = ""
        solution_method = clean_text(
            ticket.get("solutionMethod") or issue.get("solutionMethod")
        )

        explicit_actions = []
        for value in (
            ticket.get("solution"),
            issue.get("solution"),
            ticket.get("resolution_action"),
        ):
            rendered = clean_text(value)
            if rendered and rendered.casefold() not in {
                "change",
                "repair",
                "fixed",
                "решен",
                "решена",
            }:
                explicit_actions.append(rendered)
        checks: list[str] = []
        comment_actions: list[str] = []
        participant_notes: list[str] = []
        if isinstance(comments, list):
            for comment in comments:
                value = useful_description(
                    clean_tracker_comment(
                        comment.get("text") if isinstance(comment, dict) else comment
                    )
                )
                if not value or re.search(
                    r"(?i)задача скопирована|перенесен[ао]? в очередь", value
                ):
                    continue
                participant_notes.append(value)
                if re.search(
                    r"(?i)\b(?:провер|тест|диагност|измер|работает|видит|сопротивлен|напряжен)\w*",
                    value,
                ):
                    checks.append(value)
                if not re.search(
                    r"(?i)\b(?:не|нужно|надо|следует|требуется)\b", value
                ) and re.search(
                    r"(?i)\b(?:заменил[аи]?|замен[её]н[аоы]?|установил[аи]?|установлен[аоы]?|"
                    r"очистил[аи]?|очищен[аоы]?|подключил[аи]?|подключен[аоы]?|снял[аи]?|снят[аоы]?|"
                    r"затянул[аи]?|отремонтировал[аи]?|переподключил[аи]?|выполнен[аоы]?)\b",
                    value,
                ):
                    comment_actions.append(value)
        actions = list(dict.fromkeys(explicit_actions + comment_actions))
        checks = list(dict.fromkeys(checks))
        participant_notes = list(dict.fromkeys(participant_notes))
        status_values = displays(ticket.get("status") or issue.get("status"))
        status = " ".join(status_values)
        evidence = ([] if administrative_summary else [summary]) + [
            symptom,
            *components,
            defect_text,
            *actions,
            *checks,
            *participant_notes,
        ]
        if not any(len(re.sub(r"\W", "", value)) >= 6 for value in evidence):
            self.skipped["bare_issue_key"] += 1
            self.quarantine(relative, "insufficient_ticket_evidence", key or str(index))
        else:
            fields = []
            if key:
                fields.append(f"Номер тикета: {key}")
            if symptom:
                fields.append(f"Симптом: {symptom}")
            elif summary:
                fields.append(f"Симптом: {summary}")
            if components:
                fields.append(f"Компонент: {', '.join(components)}")
            if defect_text:
                fields.append(f"Код дефекта: {defect_text}")
            if solution_method:
                fields.append(f"Указанный метод решения: {solution_method}")
            if actions:
                fields.append(f"Зафиксированное действие: {'; '.join(actions)}")
            if checks:
                fields.append(f"Запись проверки: {'; '.join(checks)}")
            if participant_notes:
                fields.append(
                    f"Записи участников (неподтверждённые): {'; '.join(participant_notes)}"
                )
            limitations = []
            if status and re.search(r"(?i)закрыт|решен|closed|fixed", status):
                limitations.append(
                    "закрытие тикета само по себе не подтверждает успешность ремонта"
                )
            if not actions:
                limitations.append("действие по ремонту не зафиксировано")
            if limitations:
                fields.append(f"Ограничения: {'; '.join(limitations)}")
            content = self.redactor.redact("\n\n".join(fields), names)
            title_summary = (
                symptom.splitlines()[0]
                if administrative_summary and symptom
                else summary
                or (symptom.splitlines()[0] if symptom else "ремонт без темы")
            )
            title = (
                f"Ремонт {key}: {title_summary}"
                if key
                else f"Ремонт {index or stable_id(relative.as_posix(), 8)}: {title_summary}"
            )
            self.add(
                relative,
                title[:240],
                content,
                "ticket",
                f"#ticket-{stable_id(key or str(index), 12)}",
            )

        attachments = ticket.get("attachments", [])
        if isinstance(attachments, list):
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    self.unavailable_attachments += 1
                    continue
                candidate = attachment.get("path") or attachment.get("file")
                if not isinstance(candidate, str):
                    self.unavailable_attachments += 1
                    continue
                resolved = (self.root / candidate).resolve()
                if not resolved.is_relative_to(self.root) or not resolved.is_file():
                    self.unavailable_attachments += 1

    def add_generic_json(self, relative: Path, item: Any, index: int) -> None:
        if (
            isinstance(item, list)
            and item
            and all(isinstance(pair, list) and len(pair) == 2 for pair in item)
        ):
            fields = {clean_text(pair[0]): clean_text(pair[1]) for pair in item}
            defect = next(
                (
                    value
                    for name, value in fields.items()
                    if "код дефекта" in name.casefold() and value
                ),
                "",
            )
            if defect:
                match = re.match(
                    r"\s*([A-ZА-Я0-9]+-[A-ZА-Я0-9]+)\s*[-—:]\s*(.+)", defect
                )
                title = (
                    f"Справочник кодов: {match.group(1)} — {match.group(2)}"
                    if match
                    else f"Справочник кодов: {defect}"
                )
                self.add(
                    relative,
                    title,
                    f"Код дефекта: {defect}",
                    "note",
                    f"#record-{index:06d}",
                )
                return
        if isinstance(item, dict):
            selected = []
            for key in (
                "title",
                "name",
                "summary",
                "description",
                "text",
                "content",
                "note",
                "body",
            ):
                if key in item and flatten_text(item[key]).strip():
                    selected.append(f"{key}: {flatten_text(item[key]).strip()}")
            content = "\n\n".join(selected)
        elif isinstance(item, str):
            content = item
        elif isinstance(item, list):
            values = []
            stack = list(reversed(item))
            while stack and sum(map(len, values)) < MAX_CONTENT_CHARS * 4:
                value = stack.pop()
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
                elif isinstance(value, list):
                    stack.extend(reversed(value))
                elif isinstance(value, dict):
                    stack.extend(reversed(list(value.values())))
            content = "\n".join(values)
        else:
            content = ""
        if ISSUE_KEY_RE.fullmatch(content.strip()):
            self.skipped["bare_issue_key"] += 1
            self.quarantine(relative, "insufficient_ticket_evidence", content.strip())
            return
        if content:
            self.add(
                relative, f"Заметка {index}", content, "note", f"#record-{index:06d}"
            )


def ensure_safe_paths(source: Path, output: Path) -> tuple[Path, Path]:
    if source.is_symlink() or not source.is_dir():
        raise ValueError("input must be an existing real directory")
    source = source.resolve()
    candidate = output.absolute()
    for parent in [candidate, *candidate.parents]:
        if parent.exists() and parent.is_symlink():
            raise ValueError("output path may not contain symlinks")
        if parent.exists():
            break
    output.mkdir(parents=True, exist_ok=True)
    return source, output.resolve()


def prepare_output(output: Path) -> None:
    for path in output.iterdir():
        if path.is_symlink():
            raise ValueError("output directory contains a symlink")
        if (
            path.name == "seed.jsonl"
            or (path.name.startswith("part-") and path.suffix == ".jsonl")
            or path.name in {"manifest.json", "source-map.private.json"}
        ):
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("Данные для базы"))
    parser.add_argument("--output", type=Path, default=Path("output/knowledge-seed"))
    parser.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--part-max-bytes", type=int, default=DEFAULT_PART_BYTES)
    args = parser.parse_args(argv)
    if not 128 <= args.chunk_chars <= MAX_CONTENT_CHARS:
        parser.error(f"--chunk-chars must be between 128 and {MAX_CONTENT_CHARS}")
    if args.part_max_bytes < 512:
        parser.error("--part-max-bytes must be at least 512")
    try:
        source, output = ensure_safe_paths(args.input, args.output)
        prepare_output(output)
        builder = Builder(source, output, args.chunk_chars, args.part_max_bytes)
        manifest = builder.build()
        source_map = {
            "schema": 1,
            "private": True,
            "sources": builder.source_map,
        }
        source_map_tmp = output / ".source-map.private.json.tmp"
        source_map_tmp.write_text(
            json.dumps(source_map, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        source_map_tmp.replace(output / "source-map.private.json")
        temporary = output / ".manifest.json.tmp"
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(output / "manifest.json")
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"knowledge seed build failed: {type(error).__name__}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"documents": manifest["documents"], "parts": len(manifest["parts"])}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
