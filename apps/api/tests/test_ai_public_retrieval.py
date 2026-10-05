"""Reproducible corpus regressions; no private seed, identity key or model required.

These are development examples, not a held-out assessment of repair accuracy.
Host projection is simulated only to exercise the production bundle importer.
"""

import hashlib
import json
import re
from pathlib import Path

from robopark_api.ai_models import AIDocument
from robopark_api.services.ai import bundle, knowledge
from test_ai import enable_host


def normalized(text):
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е").replace("–", "-"))


def test_shipped_public_corpus_preserves_expected_repair_evidence(
    db_session, seed_admin, test_settings, tmp_path
):
    root = Path(__file__).resolve().parents[1] / "knowledge" / "repair-v1"
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "ai_public_retrieval.json").read_text()
    )
    assert hashlib.sha256((root / "seed.jsonl").read_bytes()).hexdigest() == fixture["seed_sha256"]
    enable_host(test_settings, tmp_path)
    test_settings.ai_knowledge_bundle_path = str(root)
    manifest = json.loads((root / "manifest.json").read_text())
    for _ in range((manifest["documents"] + 24) // 25 + 1):
        status = bundle.step(db_session, test_settings)
        assert not status["error"], status
        if status["state"] == "ready":
            break
    assert status["state"] == "ready", status
    assert status["created"] == manifest["documents"]

    for case in fixture["cases"]:
        hits = knowledge.search(db_session, seed_admin, case["query"], limit=3)
        expected = [
            hit
            for hit in hits
            if db_session.get(AIDocument, hit["id"]).source_ref in case["sources"]
        ]
        assert expected, (case["id"], "expected source missing")
        assert any(
            all(normalized(anchor) in normalized(hit["excerpt"]) for anchor in case["evidence"])
            for hit in expected
        ), (case["id"], "required evidence missing")
        assert all(len(hit["excerpt"]) <= 2400 for hit in hits), case["id"]
