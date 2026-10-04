"""Real PostgreSQL coverage for shipped-knowledge reconciliation receipts."""

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from robopark_api.ai_models import AIBundleDocument, AIDocument
from robopark_api.services.ai import bundle

from .test_schema_and_workflows import migrated_engine  # noqa: F401

pytestmark = pytest.mark.postgres


def _write_bundle(root, source_ref, *, kind="note", content="Check camera connector"):
    root.mkdir(exist_ok=True)
    record = {
        "source_ref": source_ref,
        "title": "Camera procedure",
        "content": content,
        "kind": kind,
    }
    raw = (json.dumps(record) + "\n").encode()
    (root / "seed.jsonl").write_bytes(raw)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "bundle_id": "repair-public-v1",
                "documents": 1,
                "parts": [
                    {
                        "path": "seed.jsonl",
                        "documents": 1,
                        "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                ],
            }
        )
    )


def test_real_postgres_bundle_updates_same_managed_document(
    migrated_engine,  # noqa: F811
    test_settings,
    tmp_path,
):
    state = tmp_path / "runtime.json"
    state.write_text(
        json.dumps(
            {
                "schema": 1,
                "supported": True,
                "installed": True,
                "enabled": True,
                "ready": True,
                "backend": "cuda",
            }
        )
    )
    root = tmp_path / "bundle"
    source_ref = "public:repair-v1:" + uuid4().hex
    test_settings.ai_runtime_state_path = str(state)
    test_settings.ai_knowledge_bundle_path = str(root)
    _write_bundle(root, source_ref)

    assert {
        index["name"]: index["column_names"]
        for index in inspect(migrated_engine).get_indexes("ai_documents")
    }["ix_ai_documents_source_ref_park_id"] == ["source_ref", "park_id"]

    with Session(migrated_engine) as db:
        assert bundle.step(db, test_settings)["state"] == "ready"
        row = db.scalar(select(AIDocument).where(AIDocument.source_ref == source_ref))
        original_id, original_key = row.id, row.source_key

    _write_bundle(root, source_ref, kind="manual", content="Replace camera connector")
    with Session(migrated_engine) as db:
        assert bundle.step(db, test_settings)["state"] == "ready"
        row = db.scalar(select(AIDocument).where(AIDocument.source_ref == source_ref))
        receipt = db.get(AIBundleDocument, source_ref)
        assert (row.id, row.source_key, row.kind, row.content, row.revision) == (
            original_id,
            original_key,
            "manual",
            "Replace camera connector",
            2,
        )
        assert receipt.document_id == row.id
        assert receipt.applied_document_revision == row.revision
