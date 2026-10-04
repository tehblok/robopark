#!/usr/bin/env python3
"""Build a private, redacted JSONL seed for the local knowledge base.

Source files are evidence: their contents are parsed as data and never executed.
The generated directory is intended to stay under the repository's ignored output/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from xml.etree import ElementTree


MAX_CONTENT_CHARS = 100_000
DEFAULT_CHUNK_CHARS = 12_000
DEFAULT_PART_BYTES = 64 * 1024 * 1024 - 64 * 1024
MAX_SOURCE_BYTES = 768 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED = 64 * 1024 * 1024
SUPPORTED = {".md", ".pdf", ".json", ".jsonl", ".docx"}

EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}(?![\w.-])")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?7|8)[\s()\-]*\d{3}[\s()\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\w)")
SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|authorization)\b(\s*[:=]\s*)([^\s,;]+)"
)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
URL_AUTH_RE = re.compile(r"(?i)\b(https?://)([^/@\s:]+):([^/@\s]+)@")
URL_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:token|access_token|api[_-]?key|password|secret)=)[^&#\s]+"
)


def stable_id(value: str, length: int = 20) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:length]


def source_id(relative: Path) -> str:
    return f"source:{stable_id(relative.as_posix())}"


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten_text(item.get("text", "") if isinstance(item, dict) else item) for item in value)
    if isinstance(value, dict):
        return flatten_text(value.get("text", value.get("body", "")))
    return "" if value is None else str(value)


class Redactor:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def redact(self, text: str, names: Iterable[str] = ()) -> str:
        def replace(pattern: re.Pattern[str], replacement: str, key: str, value: str) -> str:
            def repl(_: re.Match[str]) -> str:
                self.counts[key] += 1
                return replacement
            return pattern.sub(repl, value)

        text = replace(URL_AUTH_RE, r"\1[учётные данные удалены]@", "url_credentials", text)
        text = replace(URL_SECRET_QUERY_RE, r"\1[секрет удалён]", "url_credentials", text)
        text = replace(BEARER_RE, "Bearer [секрет удалён]", "secret", text)

        def secret(match: re.Match[str]) -> str:
            self.counts["secret"] += 1
            return f"{match.group(1)}{match.group(2)}[секрет удалён]"

        text = SECRET_RE.sub(secret, text)
        text = replace(EMAIL_RE, "[email удалён]", "email", text)
        text = replace(PHONE_RE, "[телефон удалён]", "phone", text)
        for name in sorted({n.strip() for n in names if isinstance(n, str) and len(n.strip()) >= 2}, key=len, reverse=True):
            hits = text.count(name)
            if hits:
                text = text.replace(name, "[имя удалено]")
                self.counts["name"] += hits
        return text


def chunks(text: str, limit: int) -> Iterator[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    while text:
        if len(text) <= limit:
            yield text
            return
        cut = max(text.rfind("\n\n", 0, limit + 1), text.rfind("\n", 0, limit + 1), text.rfind(" ", 0, limit + 1))
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
        self.parts.append({
            "path": self.path.name,
            "documents": self.count,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
        self.handle = None

    def write(self, document: dict[str, str]) -> None:
        line = (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
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
        pattern = re.compile(rf'"{re.escape(key)}"\s*:\s*\[') if key else re.compile(r"^\s*\[")
        while not start:
            piece = stream.read(1024 * 1024)
            if not piece:
                return
            buffer += piece
            match = pattern.search(buffer)
            if match:
                buffer = buffer[match.end():]
                start = True
            elif len(buffer) > 4 * 1024 * 1024:
                raise ValueError(f"array key {key!r} was not found near the document start")
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
    def __init__(self, root: Path, output: Path, chunk_chars: int, part_max_bytes: int) -> None:
        self.root = root
        self.output = output
        self.chunk_chars = min(chunk_chars, MAX_CONTENT_CHARS)
        self.writer = SeedWriter(output, part_max_bytes)
        self.redactor = Redactor()
        self.coverage: dict[str, Counter[str]] = defaultdict(Counter)
        self.skipped: Counter[str] = Counter()
        self.kinds: Counter[str] = Counter()
        self.errors: Counter[str] = Counter()
        self.unavailable_attachments = 0
        self.canonical_tickets: set[str] = set()

    def add(self, relative: Path, title: str, content: str, kind: str, suffix: str = "") -> None:
        content = self.redactor.redact(content)
        pieces = list(chunks(content, self.chunk_chars))
        for number, piece in enumerate(pieces, 1):
            ref = source_id(relative) + suffix
            if len(pieces) > 1:
                ref += f"#part-{number:04d}"
            self.writer.write({"title": self.redactor.redact(title), "content": piece, "kind": kind, "source_ref": ref})
            self.kinds[kind] += 1

    def build(self) -> dict[str, Any]:
        # The ticket export includes the same repairs in dataset.jsonl and a
        # verbose Markdown rendering. Prefer structured, identity-redacted
        # records over repeated metadata/changelog dumps.
        for path, _relative in self.walk(count_skipped=False):
            if path.name != "dataset.jsonl" or path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(item, dict) and {"key", "issue", "comments"} <= item.keys() and isinstance(item["key"], str):
                        self.canonical_tickets.add(item["key"])
        for path, relative in self.walk():
            extension = path.suffix.lower()
            self.coverage[extension]["discovered"] += 1
            if extension not in SUPPORTED:
                self.skipped["unsupported_format"] += 1
                self.coverage[extension]["skipped"] += 1
                continue
            if extension == ".md" and path.stem in self.canonical_tickets:
                self.skipped["duplicate_ticket_rendering"] += 1
                self.coverage[extension]["skipped"] += 1
                continue
            try:
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    raise ValueError("source_too_large")
                getattr(self, f"read_{extension[1:]}")(path, relative)
                self.coverage[extension]["processed"] += 1
            except Exception as error:
                reason = str(error) if str(error) in {"source_too_large", "encrypted_pdf"} else "parse_error"
                self.errors[reason] += 1
                self.coverage[extension]["skipped"] += 1
        self.writer.finish()
        return {
            "schema": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_fingerprint": self.fingerprint(),
            "documents": self.writer.total,
            "parts": self.writer.parts,
            "documents_by_kind": dict(sorted(self.kinds.items())),
            "coverage": {key: dict(value) for key, value in sorted(self.coverage.items())},
            "skipped_by_reason": dict(sorted(self.skipped.items())),
            "unavailable_attachments": self.unavailable_attachments,
            "redactions": dict(sorted(self.redactor.counts.items())),
            "errors": dict(sorted(self.errors.items())),
        }

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
                        continue
                    if entry.is_dir(follow_symlinks=False):
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
            names[:] = sorted(name for name in names if not (Path(directory) / name).is_symlink())
            for name in sorted(filenames):
                path = Path(directory) / name
                if path.is_symlink():
                    continue
                relative = path.relative_to(self.root)
                digest.update(relative.as_posix().encode())
                digest.update(str(path.stat().st_size).encode())
        return digest.hexdigest()

    def read_md(self, path: Path, relative: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        heading = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#") and line.lstrip("# ").strip()), "")
        kind = "ticket" if re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", path.stem) else "note"
        # File format does not establish authority. Markdown exports and AI
        # skills remain experience/notes; only dedicated repair manuals carry
        # the manual kind. Administrator decides activation during import.
        self.add(relative, heading or f"Материал {stable_id(relative.as_posix(), 8)}", text, kind)

    def read_docx(self, path: Path, relative: Path) -> None:
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_UNCOMPRESSED:
                raise ValueError("source_too_large")
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        paragraphs = []
        for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
            value = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
            if value.strip():
                paragraphs.append(value.strip())
        self.add(relative, f"Документ {stable_id(relative.as_posix(), 8)}", "\n\n".join(paragraphs), "manual")

    def read_pdf(self, path: Path, relative: Path) -> None:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ValueError("pdf_reader_unavailable") from error
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf")
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                self.add(relative, f"PDF-инструкция {stable_id(relative.as_posix(), 8)}, стр. {page_number}", text, "manual", f"#page-{page_number:04d}")

    def read_json(self, path: Path, relative: Path) -> None:
        shape = json_shape(path)
        if shape == "telegram":
            self.read_telegram(path, relative)
        elif shape == "tickets":
            for ticket in iter_array(path, "tickets"):
                self.add_ticket(relative, ticket)
        elif shape == "list":
            for index, item in enumerate(iter_array(path), 1):
                if isinstance(item, dict) and ({"summary", "description", "comments", "key"} & set(item)):
                    self.add_ticket(relative, item, index)
                else:
                    self.add_generic_json(relative, item, index)
        else:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            self.add_generic_json(relative, data, 1)

    def read_jsonl(self, path: Path, relative: Path) -> None:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for index, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, dict) and ({"summary", "description", "comments", "key"} & set(item)):
                    self.add_ticket(relative, item, index)
                else:
                    self.add_generic_json(relative, item, index)

    def read_telegram(self, path: Path, relative: Path) -> None:
        names: set[str] = set()
        parent: dict[Any, Any] = {}
        for message in iter_array(path, "messages"):
            if not isinstance(message, dict):
                continue
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

        branches: dict[Any, list[str]] = defaultdict(list)
        for message in iter_array(path, "messages"):
            if not isinstance(message, dict) or message.get("type", "message") != "message":
                continue
            text = flatten_text(message.get("text", "")).strip()
            if not text:
                continue
            file_ref = message.get("file") or message.get("photo")
            if isinstance(file_ref, str):
                attachment = (path.parent / file_ref).resolve()
                if not attachment.is_relative_to(self.root) or not attachment.is_file():
                    self.unavailable_attachments += 1
            text = self.redactor.redact(text, names)
            root = root_of(message.get("id"))
            branches[root].append(text)
        for root, messages in branches.items():
            branch_hash = stable_id(f"{relative.as_posix()}:{root}", 12)
            self.add(relative, "Переписка по рабочему вопросу", "\n\n".join(messages), "chat", f"#thread-{branch_hash}")

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
                    for key in ("author", "from", "name", "display_name", "createdBy", "updatedBy"):
                        names.update(identity_values(comment.get(key)))
        fields = []
        labels = (
            ("key", "Номер"), ("summary", "Тема"), ("title", "Тема"),
            ("description", "Описание"), ("status", "Статус"), ("component", "Компонент"),
            ("components", "Компоненты"), ("defect", "Дефект"), ("defect_code", "Код дефекта"),
            ("solution", "Решение"), ("resolution", "Результат"),
        )
        for key, label in labels:
            value = ticket.get(key, issue.get(key))
            if value not in (None, "", []):
                rendered = flatten_text(value) if not isinstance(value, (list, dict)) else json.dumps(value, ensure_ascii=False)
                fields.append(f"{label}: {rendered}")
        for key, label in (("solutionMethod", "Способ решения"), ("theDefectCode", "Код дефекта")):
            value = issue.get(key)
            if value is None:
                value = next((value for name, value in issue.items() if name.endswith("--" + key)), None)
            if value not in (None, "", []):
                fields.append(f"{label}: {flatten_text(value)}")
        if isinstance(comments, list):
            for comment in comments:
                value = flatten_text(comment)
                if value.strip():
                    fields.append(f"Комментарий: {value.strip()}")
        content = self.redactor.redact("\n\n".join(fields), names)
        if content.strip():
            key = flatten_text(ticket.get("key", "")).strip()
            title = f"Заявка {key}" if key else f"Заявка {index or stable_id(relative.as_posix(), 8)}"
            self.add(relative, title, content, "ticket", f"#ticket-{stable_id(key or str(index), 12)}")

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
        if isinstance(item, dict):
            selected = []
            for key in ("title", "name", "summary", "description", "text", "content", "note", "body"):
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
        if content:
            self.add(relative, f"Заметка {index}", content, "note", f"#record-{index:06d}")


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
        if path.name == "seed.jsonl" or (path.name.startswith("part-") and path.suffix == ".jsonl") or path.name == "manifest.json":
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
        temporary = output / ".manifest.json.tmp"
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output / "manifest.json")
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"knowledge seed build failed: {type(error).__name__}", file=sys.stderr)
        return 2
    print(json.dumps({"documents": manifest["documents"], "parts": len(manifest["parts"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
