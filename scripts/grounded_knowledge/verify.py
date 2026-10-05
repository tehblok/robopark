"""Check exact quotations and provenance; semantic correctness still needs review."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from scripts.build_authored_knowledge import PRIVATE_MARKERS

ROLES = {"mechanic", "operator", "admin"}


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def load_sources(root):
    root = Path(root)
    manifest = json.loads((root / "manifest.private.json").read_text())
    path = root / "sources.jsonl"
    raw = path.read_bytes()
    if (
        manifest.get("errors")
        or hashlib.sha256(raw).hexdigest() != manifest["sourcebook_sha256"]
    ):
        raise ValueError("grounding_sourcebook_changed")
    sources = {}
    for line in raw.splitlines():
        row = json.loads(line)
        if row["source_ref"] in sources:
            raise ValueError("grounding_source_duplicate")
        if hashlib.sha256(row["text"].encode()).hexdigest() != row["text_sha256"]:
            raise ValueError("grounding_source_text_changed")
        sources[row["source_ref"]] = row
    if len(sources) != manifest["records"]:
        raise ValueError("grounding_source_count")
    return sources


def verify_articles(articles, sources):
    findings, counts, used = [], Counter(), set()
    for article in articles:
        record = article["evidence"]
        identifier = article["id"]

        def fail(reason, article_id=identifier, **details):
            findings.append({"article": article_id, "reason": reason, **details})

        for key in ("roles", "questions", "entities"):
            value = record.get(key)
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                fail("metadata_missing", field=key)
        # These fields are rendered outside the restricted source register.
        public_values = [record.get("scope", ""), article.get("title", "")]
        public_values += record.get("questions", []) + record.get("entities", [])
        public_values += [claim.get("claim", "") for claim in record["claims"]]
        if any(
            PRIVATE_MARKERS.search(value)
            for value in public_values
            if isinstance(value, str)
        ):
            fail("reader_metadata_private")
        roles = record.get("roles", [])
        if not isinstance(roles, list) or not set(roles) <= ROLES:
            fail("role_invalid")
        if not isinstance(record.get("scope"), str) or not record["scope"].strip():
            fail("scope_missing")
        supported = set()
        for index, claim in enumerate(record["claims"]):
            if claim["basis"] not in {"manual", "observed"}:
                fail("unsupported_editorial_claim", claim=index)
            for citation in claim["sources"]:
                ref = citation["source_ref"]
                source = sources.get(ref)
                if source is None:
                    fail("source_not_found", source_ref=ref, claim=index)
                    continue
                quote = normalize(citation["excerpt"])
                if not quote or quote not in normalize(source["text"]):
                    fail("quote_not_exact", source_ref=ref, claim=index)
                    continue
                if citation["locator"] != source["locator"]:
                    fail(
                        "locator_mismatch",
                        source_ref=ref,
                        claim=index,
                        expected=source["locator"],
                    )
                if claim["basis"] == "manual" and source["kind"] not in {
                    "document",
                    "reference",
                }:
                    fail("conversation_is_not_manual", source_ref=ref, claim=index)
                used.add(ref)
                supported.add(ref)
                counts[source["kind"]] += 1
        for alias, refs in record["citations"].items():
            if not set(refs) <= supported:
                fail("citation_without_verified_quote", citation=alias)
        if not set(record["source_refs"]) <= supported:
            fail("unused_or_unverified_source_refs")
    return {
        "valid": not findings,
        "findings": findings,
        "articles": len(articles),
        "claims": sum(len(article["evidence"]["claims"]) for article in articles),
        "source_kinds": dict(counts),
        "unique_sources": len(used),
        "semantic_correctness_proven": False,
    }


def verify_original_files(sources, articles, originals):
    """Do not attach a stale ledger to newly replaced source files."""
    originals = Path(originals).resolve()
    refs = {ref for article in articles for ref in article["evidence"]["source_refs"]}
    checked = {}
    for ref in sorted(refs):
        row = sources[ref]
        relative = row["relative_path"]
        path = originals / relative
        if path.is_symlink() or not path.resolve().is_relative_to(originals):
            raise ValueError("grounding_original_path_invalid")
        if relative not in checked:
            checked[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        if checked[relative] != row["file_sha256"]:
            raise ValueError("grounding_original_changed")
    return len(checked)
