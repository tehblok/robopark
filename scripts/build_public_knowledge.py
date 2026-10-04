#!/usr/bin/env python3
"""Export repair evidence for public distribution; raw conversations stay private."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import unicodedata
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_knowledge_seed import iter_array, json_shape


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe"}:
            self.hidden += 1
        elif not self.hidden and tag in {"div", "p", "br", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"div", "p", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(text):
    text = re.sub(r"\[([^]\n]+)\]\([^\n)]*\)", r"\1", text)
    parser = PlainText()
    parser.feed(text)
    parser.close()
    return "".join(parser.parts)


MARK = "[удалено]"
SHEETS = {".xlsx", ".xlsm", ".xls", ".csv"}
MANUALS = {".pdf", ".docx"}
TICKET_FIELDS = (
    "Симптом",
    "Компонент",
    "Код дефекта",
    "Указанный метод решения",
    "Зафиксированное действие",
)
DROP_HEADER = re.compile(
    r"(?i)^(?:робот|список роботов|номер робота|id)$|\b(?:(?:supplier|item)\s+id|owner|uid|url|автор|сотрудник|исполнитель|логин|пароль)\b"
)
CREDENTIAL = re.compile(
    r"(?im)\b(?:password|passwd|pwd|token|api[_-]?key|secret|authorization|парол[ьиья]|секрет|логин|пин(?:код)?|код\s+доступа|ssid)\s*[:=]\s*[^\n]+"
)
STATIC = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [^-]*(?:PRIVATE KEY|SECRET)[^-]*-----.*?-----END [^-]+-----",
            re.DOTALL,
        ),
    ),
    ("credential", CREDENTIAL),
    (
        "token",
        re.compile(
            r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+|\b(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]{8,}|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
        ),
    ),
    ("url", re.compile(r"(?i)\b(?:https?|ftp|ssh|file)://[^\s<>]+")),
    ("email", re.compile(r"(?i)[\w.+-]+@[\w.-]+\.[a-zа-я]{2,}")),
    (
        "phone",
        re.compile(
            r"(?<!\w)(?:\+\d{1,3}[\s(-]*\d[\d\s()-]{7,}\d|8\s*\(\d{3}\)\s*\d{3}[ -]\d{2}[ -]\d{2}|[78]\d{10})(?!\w)"
        ),
    ),
    ("handle", re.compile(r"(?<![\w@])@[\w.-]{2,}")),
    (
        "domain",
        re.compile(
            r"(?i)(?<![\w.])(?:[a-z0-9_-]+\.)+(?:ru|com|org|net|am|io|dev|local|internal|team|co|center|cloud|ai|app|site|online|store|tech|info|biz|me|pro|xyz|рф)\b(?:/[^\s<>]*)?"
        ),
    ),
    ("mac", re.compile(r"(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b")),
    ("uuid", re.compile(r"(?i)\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b")),
    (
        "path",
        re.compile(r"(?<!\w)(?:~?/(?:[\w.-]+/)+[^\s<>;,]*|[A-Za-z]:\\[^\s<>;,]+)"),
    ),
)
IP = re.compile(
    r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|(?<!\w)(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:]{0,4}(?!\w)"
)
IDENTITY_KEYS = {
    "assignee",
    "createdBy",
    "updatedBy",
    "resolvedBy",
    "author",
    "from",
    "from_name",
    "actor",
    "followers",
}
IDENTITY_VALUE_KEYS = {"display", "display_name", "name", "login", "username"}


def normalize(value):
    return unicodedata.normalize("NFKC", str(value)).replace("\x00", "")


def identity_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from identity_values(child)
    elif isinstance(value, dict):
        for key in IDENTITY_VALUE_KEYS:
            if isinstance(value.get(key), str):
                yield value[key]


class PublicPolicy:
    def __init__(
        self,
        *,
        identity_key,
        identities=(),
        ticket_prefixes=(),
        asset_ids=(),
        source_extensions=None,
    ):
        if not isinstance(identity_key, bytes) or len(identity_key) < 32:
            raise ValueError("publication_identity_key_invalid")
        self.identity_key = identity_key
        self.identities = set(identities)
        self.ticket_prefixes = set(ticket_prefixes)
        self.asset_ids = set(asset_ids)
        self.source_extensions = source_extensions or {}
        self.counts = Counter()
        self.rejected = Counter()
        self._patterns = None

    def collect(self, value, context=""):
        if isinstance(value, list):
            for child in value:
                self.collect(child, context)
        elif isinstance(value, dict):
            for key, child in value.items():
                identity_key = key in IDENTITY_KEYS - {"from", "from_name"}
                message_sender = key in {
                    "from",
                    "from_name",
                    "name",
                    "display_name",
                } and (
                    context in {"messages", "comments"}
                    or value.get("type") == "message"
                    or "date_unixtime" in value
                )
                if identity_key or message_sender or key.endswith("reporterRC"):
                    self.identities.update(identity_values(child))
                if key in {"robot", "rover", "robot_id", "rover_id"}:
                    for token in identity_values(child):
                        self.asset_ids.update(re.findall(r"(?i)\b[a-z]\d{3,}\b", token))
                if key in {"key", "issue_key"} and isinstance(child, str):
                    match = re.fullmatch(r"([A-Z][A-Z0-9_]{2,})-\d+", child)
                    if match:
                        self.ticket_prefixes.add(match[1])
                # Only metadata-labelled identities; component display names
                # must never be interpreted as people.
                self.collect(child, key)
        self._patterns = None

    def _literals(self):
        if self._patterns is None:
            identities = {
                normalize(x).strip()
                for x in self.identities
                if len(normalize(x).strip()) >= 2
            }
            patterns = []
            for name, values in (("identity", identities), ("asset", self.asset_ids)):
                if values:
                    patterns.append(
                        (
                            name,
                            re.compile(
                                r"(?<!\w)(?:"
                                + "|".join(
                                    re.escape(x)
                                    for x in sorted(values, key=lambda x: (-len(x), x))
                                )
                                + r")(?!\w)",
                                re.IGNORECASE,
                            ),
                        )
                    )
            if self.ticket_prefixes:
                patterns.append(
                    (
                        "ticket",
                        re.compile(
                            r"(?i)\b(?:"
                            + "|".join(
                                re.escape(x) for x in sorted(self.ticket_prefixes)
                            )
                            + r")-\d+\b"
                        ),
                    )
                )
            self._patterns = patterns
        return self._patterns

    def clean(self, text):
        text = plain(normalize(text))
        for name, pattern in (*STATIC, *self._literals()):
            text, count = pattern.subn(MARK, text)
            self.counts[name] += count

        def ip(match):
            try:
                ipaddress.ip_address(match[0])
            except ValueError:
                return match[0]
            self.counts["ip"] += 1
            return MARK

        text = IP.sub(ip, text)
        return text.strip()

    def prepare(self, value):
        ref = value["source_ref"]
        ext = self.source_extensions.get(ref.split("#", 1)[0])
        kind = value["kind"]
        if kind == "chat":
            self.rejected["raw_chat"] += 1
            return None
        title, content = value["title"], value["content"]
        if kind == "ticket" and ext in {".jsonl", ".json"}:
            fields = {}
            for block in content.split("\n\n"):
                name, colon, text = block.partition(":")
                if colon and name in TICKET_FIELDS:
                    # Machine templates can be hidden inside HTML in a symptom.
                    # Keep these cases private rather than guessing its bounds.
                    if re.search(
                        r"(?i)\{%|Robot\s+History|Failure\s+reaction\s+level", text
                    ):
                        self.rejected["tracker_template"] += 1
                        return None
                    fields[name] = self.clean(text.strip())
                    if re.search(r"(?<!\d)\d{8,}(?!\d)", fields[name]):
                        self.rejected["numeric_identifier"] += 1
                        return None
                    if "\n\n" in fields[name]:
                        self.rejected["nested_ticket_blocks"] += 1
                        return None
            if not fields.get("Симптом") or not any(
                fields.get(k) for k in TICKET_FIELDS[1:]
            ):
                self.rejected["unstructured_ticket"] += 1
                return None
            title = (
                "Ремонтный случай: "
                + (
                    fields.get("Компонент")
                    or fields.get("Код дефекта")
                    or "диагностика"
                )[:170]
            )
            content = "\n\n".join(
                f"{key}: {fields[key]}" for key in TICKET_FIELDS if fields.get(key)
            )
            content += "\n\nОграничения: историческая запись не подтверждает успешность ремонта и не является универсальной инструкцией."
        elif (
            kind == "manual"
            and ext in MANUALS
            or kind == "note"
            and ext in MANUALS
            and title.startswith("Неполная инструкция:")
        ):
            pass
        elif kind == "note" and ext in SHEETS and title.startswith("Справочник:"):
            lines = [
                line.strip()
                for line in content.splitlines()
                if line.strip()
                and not line.startswith(("Контекст документа:", "Название:"))
            ]
            if not lines or any(":" not in line for line in lines):
                self.rejected["unstructured_table"] += 1
                return None
            lines = [
                line
                for line in lines
                if not DROP_HEADER.search(line.split(":", 1)[0].strip())
            ]
            if len(lines) < 2:
                self.rejected["table_without_public_fields"] += 1
                return None
            # The original table title may contain a robot, owner or supplier ID.
            title = "Справочник: " + self.clean(lines[0])[:175]
            content = "\n".join(lines)
        else:
            self.rejected["source_not_allowlisted"] += 1
            return None
        title, content = self.clean(title), self.clean(content)
        if len(re.sub(r"\W|\[удалено\]", "", content)) < 12:
            self.rejected["empty_after_redaction"] += 1
            return None
        public_ref = (
            "public:repair-v1:"
            + hmac.new(
                self.identity_key,
                ("robopark-public-repair-v1\0" + ref).encode(),
                hashlib.sha256,
            ).hexdigest()[:32]
        )
        return {
            "title": title[:240],
            "content": content,
            "kind": kind,
            "source_ref": public_ref,
        }


def read_records(path):
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
    else:
        shape = json_shape(path)
        key = (
            "messages"
            if shape == "telegram"
            else "tickets"
            if shape == "tickets"
            else None
        )
        if shape in {"telegram", "tickets", "list"}:
            yield from iter_array(path, key)
        elif path.stat().st_size < 8 * 1024 * 1024:
            yield json.loads(path.read_text())


def build(
    source,
    source_root,
    output,
    report,
    identity_key_path,
    *,
    initialize_key=False,
    reference_manifest=None,
):
    if any(
        path.resolve().is_relative_to(output.resolve())
        for path in (report, identity_key_path)
    ):
        raise ValueError("private_output_inside_public_bundle")
    if identity_key_path.is_symlink():
        raise ValueError("publication_identity_key_invalid")
    if report.is_symlink():
        raise ValueError("private_report_invalid")
    identity_key_path.parent.mkdir(parents=True, exist_ok=True)
    if not identity_key_path.exists():
        if not initialize_key:
            raise ValueError("publication_identity_key_missing")
        fd = os.open(identity_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(secrets.token_bytes(32))
    identity_key = identity_key_path.read_bytes()
    if len(identity_key) != 32:
        raise ValueError("publication_identity_key_invalid")
    key_sha256 = hashlib.sha256(identity_key).hexdigest()
    for prior in (
        output / "manifest.json",
        *((reference_manifest,) if reference_manifest else ()),
    ):
        if prior.exists():
            expected = json.loads(prior.read_text()).get("identity_key_sha256")
            if expected and expected != key_sha256:
                raise ValueError("publication_identity_key_mismatch")
    manifest = json.loads((source / "manifest.json").read_text())
    mapping = json.loads((source / "source-map.private.json").read_text())
    if (
        manifest.get("schema") != 2
        or mapping.get("schema") != 1
        or mapping.get("private") is not True
    ):
        raise ValueError("private_seed_schema_invalid")
    extensions = {
        key: Path(value["relative_path"]).suffix.lower()
        for key, value in mapping["sources"].items()
    }
    policy = PublicPolicy(identity_key=identity_key, source_extensions=extensions)
    paths = sorted(
        {
            value["relative_path"]
            for value in mapping["sources"].values()
            if Path(value["relative_path"]).suffix.lower() in {".json", ".jsonl"}
        }
    )
    for relative in paths:
        path = (source_root / relative).resolve()
        if not path.is_relative_to(source_root.resolve()) or path.is_symlink():
            raise ValueError("private_source_path_invalid")
        for value in read_records(path):
            policy.collect(value)
    records = []
    for part in manifest["parts"]:
        name = part["path"]
        if not re.fullmatch(r"(?:seed|part-\d{4})\.jsonl", name):
            raise ValueError("private_seed_path_invalid")
        raw = (source / name).read_bytes()
        if (
            len(raw) != part["bytes"]
            or hashlib.sha256(raw).hexdigest() != part["sha256"]
        ):
            raise ValueError("private_seed_hash_invalid")
        records.extend(json.loads(line) for line in raw.splitlines() if line.strip())
    exported, fingerprints = [], set()
    for value in records:
        result = policy.prepare(value)
        if result is None:
            continue
        fingerprint = (result["kind"], result["title"], result["content"])
        if fingerprint in fingerprints:
            policy.rejected["duplicate_public_evidence"] += 1
            continue
        fingerprints.add(fingerprint)
        exported.append(result)
    raw = b"".join(
        (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
        for value in exported
    )
    if not raw or len(raw) > 64 * 1024 * 1024:
        raise ValueError("public_seed_size_invalid")
    public = {
        "schema": 1,
        "bundle_id": "repair-public-v1",
        "identity_key_sha256": key_sha256,
        "documents": len(exported),
        "parts": [
            {
                "path": "seed.jsonl",
                "documents": len(exported),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }
    audit = {
        "schema": 1,
        "private": True,
        "documents": len(exported),
        "by_kind": dict(Counter(x["kind"] for x in exported)),
        "rejected": dict(policy.rejected),
        "redactions": dict(policy.counts),
        "identities": sorted(policy.identities),
        "asset_ids": sorted(policy.asset_ids),
        "ticket_prefixes": sorted(policy.ticket_prefixes),
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "seed.jsonl").write_bytes(raw)
    (output / "manifest.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    return {
        "documents": len(exported),
        "by_kind": audit["by_kind"],
        "rejected": audit["rejected"],
        "redactions": audit["redactions"],
        "sha256": public["parts"][0]["sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-report", type=Path, required=True)
    parser.add_argument("--identity-key", type=Path, required=True)
    parser.add_argument(
        "--initialize-key",
        action="store_true",
        help="Explicit first bootstrap only; keep a private backup",
    )
    args = parser.parse_args()
    try:
        result = build(
            args.input,
            args.source_root,
            args.output,
            args.private_report,
            args.identity_key,
            initialize_key=args.initialize_key,
            reference_manifest=Path(__file__).resolve().parents[1]
            / "apps/api/knowledge/repair-v1/manifest.json",
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(
            "public_knowledge_export_failed: " + type(error).__name__, file=sys.stderr
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
