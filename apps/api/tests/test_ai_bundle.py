"""Bundled knowledge is bounded, resumable and respects operator decisions."""

import hashlib
import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from robopark_api.ai_models import AIBundleDocument, AIChunk, AIConfig, AIDocument, AITerm
from robopark_api.services.ai import bundle, knowledge
from test_ai import enable_host


def write_bundle(tmp_path, records, *, bundle_id="repair-public-v1"):
    root = tmp_path / "bundle"
    root.mkdir(exist_ok=True)
    raw = b"".join((json.dumps(record, ensure_ascii=False) + "\n").encode() for record in records)
    (root / "seed.jsonl").write_bytes(raw)
    manifest = {
        "schema": 1,
        "bundle_id": bundle_id,
        "documents": len(records),
        "parts": [
            {
                "path": "seed.jsonl",
                "documents": len(records),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def record(index, kind="note", **changes):
    return {
        "source_ref": "public:repair-v1:" + f"{index:032x}",
        "title": "Проверка камеры " + str(index),
        "content": "Проверить крепление камеры и разъём " + str(index),
        "kind": kind,
        **changes,
    }


def private_record(index, kind="note", **changes):
    return record(
        index,
        kind,
        source_ref="private:repair-v2:" + f"{index:032x}",
        **changes,
    )


def setup_bundle(tmp_path, settings, records):
    enable_host(settings, tmp_path)
    root = write_bundle(tmp_path, records)
    settings.ai_knowledge_bundle_path = str(root)
    return root


def count(db):
    return db.scalar(select(func.count()).select_from(AIDocument))


def test_bundle_does_no_io_or_writes_on_khadas(db_session, test_settings, tmp_path):
    test_settings.ai_knowledge_bundle_path = str(tmp_path / "missing")
    result = bundle.step(db_session, test_settings)
    assert result["state"] == "unavailable"
    assert count(db_session) == 0


def test_bundle_is_bounded_and_resumes_with_searchable_unverified_experience(
    db_session, test_settings, tmp_path, seed_admin
):
    setup_bundle(tmp_path, test_settings, [record(1, "manual"), record(2), record(3)])
    first = bundle.step(db_session, test_settings, batch_size=2)
    assert first["state"] == "importing" and first["processed"] == 2
    assert count(db_session) == 2
    assert bundle.step(db_session, test_settings, batch_size=2)["state"] == "ready"
    assert count(db_session) == 3
    assert bundle.step(db_session, test_settings)["created"] == 3
    found = knowledge.search(db_session, seed_admin, "камера")
    assert len(found) == 3 and {item["trust"] for item in found} == {"instruction", "unverified"}
    assert db_session.scalar(select(func.count()).select_from(AITerm)) > 0


def test_private_bundle_is_bounded_and_resumes_without_promoting_non_manual_metadata(
    db_session, test_settings, tmp_path, seed_admin
):
    enable_host(test_settings, tmp_path)
    root = write_bundle(
        tmp_path,
        [
            record(1, content="Расширенная публичная запись"),
            private_record(2, "manual"),
            private_record(3),
        ],
        bundle_id="repair-private-v2",
    )
    test_settings.ai_knowledge_bundle_path = str(root)

    first = bundle.step(db_session, test_settings, batch_size=2)
    assert first["state"] == "importing" and first["processed"] == 2
    assert bundle.step(db_session, test_settings, batch_size=2)["state"] == "ready"

    rows = list(db_session.scalars(select(AIDocument).order_by(AIDocument.source_ref)))
    assert len(rows) == 3
    assert {row.source_ref for row in rows} == {
        record(1)["source_ref"],
        private_record(2)["source_ref"],
        private_record(3)["source_ref"],
    }
    assert {row.source_ref: row.trust for row in rows} == {
        record(1)["source_ref"]: "unverified",
        private_record(2)["source_ref"]: "instruction",
        private_record(3)["source_ref"]: "unverified",
    }
    assert len(knowledge.search(db_session, seed_admin, "камеры")) == 3


def test_public_to_private_upgrade_reuses_public_identity_and_preserves_operator_decisions(
    db_session, test_settings, tmp_path
):
    setup_bundle(tmp_path, test_settings, [record(1), record(2), record(3)])
    assert bundle.step(db_session, test_settings)["state"] == "ready"
    rows = {
        row.source_ref: row
        for row in db_session.scalars(select(AIDocument).order_by(AIDocument.source_ref))
    }
    original_ids = {source_ref: row.id for source_ref, row in rows.items()}
    knowledge.remove(db_session, rows[record(1)["source_ref"]])
    edited = rows[record(2)["source_ref"]]
    edited.content = "Правка механика"
    edited.revision += 1
    db_session.commit()

    write_bundle(
        tmp_path,
        [
            record(1, content="Полная приватная версия 1"),
            record(2, content="Полная приватная версия 2"),
            record(3, content="Полная приватная версия 3"),
            private_record(4),
        ],
        bundle_id="repair-private-v2",
    )
    assert bundle.step(db_session, test_settings, batch_size=2)["state"] == "importing"
    assert bundle.step(db_session, test_settings, batch_size=2)["state"] == "ready"

    upgraded = {
        row.source_ref: row
        for row in db_session.scalars(select(AIDocument).order_by(AIDocument.source_ref))
    }
    assert len(upgraded) == 4
    assert {source_ref: upgraded[source_ref].id for source_ref in original_ids} == original_ids
    assert upgraded[record(1)["source_ref"]].state == "deleted"
    assert upgraded[record(2)["source_ref"]].content == "Правка механика"
    assert upgraded[record(3)["source_ref"]].content == "Полная приватная версия 3"


@pytest.mark.parametrize(
    ("bundle_id", "source_ref"),
    [
        ("repair-public-v1", "private:repair-v2:" + "1" * 32),
        ("repair-private-v2", "private:repair-v1:" + "1" * 32),
        ("repair-private-v2", "public:repair-v2:" + "1" * 32),
    ],
)
def test_bundle_rejects_source_identity_outside_manifest_namespace(
    db_session, test_settings, tmp_path, bundle_id, source_ref
):
    enable_host(test_settings, tmp_path)
    root = write_bundle(
        tmp_path,
        [record(1, source_ref=source_ref)],
        bundle_id=bundle_id,
    )
    test_settings.ai_knowledge_bundle_path = str(root)

    result = bundle.step(db_session, test_settings)
    assert result["state"] == "failed" and result["error"] == "ai_bundle_invalid"
    assert count(db_session) == 0


def test_disabled_ai_pauses_initial_import(db_session, test_settings, tmp_path):
    setup_bundle(tmp_path, test_settings, [record(1)])
    row = AIConfig(id=1, enabled=False)
    db_session.add(row)
    db_session.commit()
    assert bundle.step(db_session, test_settings)["state"] == "paused"
    assert count(db_session) == 0
    row.enabled = True
    db_session.commit()
    assert bundle.step(db_session, test_settings)["state"] == "ready"


def test_disabled_ai_does_not_read_bundle(db_session, test_settings, tmp_path, monkeypatch):
    setup_bundle(tmp_path, test_settings, [record(1)])
    db_session.add(AIConfig(id=1, enabled=False))
    db_session.commit()

    def unexpected_inspection(_settings):
        raise AssertionError("disabled AI must not inspect the bundle")

    monkeypatch.setattr(bundle, "inspect", unexpected_inspection)
    assert bundle.step(db_session, test_settings)["state"] == "paused"


def test_bundle_update_preserves_deleted_and_edited_records(
    db_session, test_settings, tmp_path, seed_admin
):
    setup_bundle(tmp_path, test_settings, [record(1), record(2), record(3)])
    bundle.step(db_session, test_settings)
    rows = list(db_session.scalars(select(AIDocument).order_by(AIDocument.source_ref)))
    knowledge.remove(db_session, rows[0])
    rows[1].content = "Собственная проверенная запись механика"
    rows[1].revision += 1
    rows[2].state = "rejected"
    rows[2].revision += 1
    db_session.commit()
    write_bundle(tmp_path, [record(1, content="Updated package"), record(4)])
    assert bundle.step(db_session, test_settings)["state"] == "ready"
    db_session.refresh(rows[0])
    db_session.refresh(rows[1])
    db_session.refresh(rows[2])
    assert rows[0].state == "deleted"
    assert rows[1].content == "Собственная проверенная запись механика"
    assert rows[1].state == "active"
    assert rows[2].state == "rejected"
    assert count(db_session) == 4


def test_bundle_update_replaces_untouched_document_and_retires_removed_documents_in_batches(
    db_session, test_settings, tmp_path
):
    setup_bundle(
        tmp_path,
        test_settings,
        [record(1), record(2), record(3)],
    )
    assert bundle.step(db_session, test_settings, batch_size=3)["state"] == "ready"
    original = db_session.scalar(
        select(AIDocument).where(AIDocument.source_ref == record(1)["source_ref"])
    )
    original_id, original_key = original.id, original.source_key

    write_bundle(
        tmp_path,
        [record(1, kind="manual", title="Новая проверка", content="Новая инструкция")],
    )
    assert bundle.step(db_session, test_settings, batch_size=1)["state"] == "retiring"
    db_session.refresh(original)
    assert (
        original.id,
        original.source_key,
        original.kind,
        original.content,
        original.revision,
    ) == (
        original_id,
        original_key,
        "manual",
        "Новая инструкция",
        2,
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(AIDocument).where(AIDocument.state == "active")
        )
        == 2
    )
    assert bundle.step(db_session, test_settings, batch_size=1)["state"] == "retiring"
    assert bundle.step(db_session, test_settings, batch_size=1)["state"] == "ready"
    retired = list(
        db_session.scalars(
            select(AIDocument).where(
                AIDocument.source_ref.in_((record(2)["source_ref"], record(3)["source_ref"]))
            )
        )
    )
    assert {row.state for row in retired} == {"candidate"}
    assert all(row.revision == 2 for row in retired)
    assert all(
        db_session.scalar(
            select(func.count()).select_from(AIChunk).where(AIChunk.document_id == row.id)
        )
        == 0
        for row in retired
    )


def test_reclassification_does_not_resurrect_deleted_bundle_document(
    db_session, test_settings, tmp_path
):
    root = setup_bundle(tmp_path, test_settings, [record(1, kind="note")])
    bundle.step(db_session, test_settings)
    row = db_session.scalar(select(AIDocument))
    original_id, original_key = row.id, row.source_key
    knowledge.remove(db_session, row)

    write_bundle(root.parent, [record(1, kind="manual", content="Новая инструкция")])
    assert bundle.step(db_session, test_settings)["state"] == "ready"
    db_session.refresh(row)
    assert (row.id, row.source_key, row.kind, row.state, row.content) == (
        original_id,
        original_key,
        "note",
        "deleted",
        "",
    )
    assert count(db_session) == 1


@pytest.mark.parametrize("operator_action", ["edit", "delete", "user_owned"])
def test_existing_document_without_receipt_is_never_adopted_after_operator_change(
    db_session, test_settings, tmp_path, seed_admin, operator_action
):
    setup_bundle(tmp_path, test_settings, [record(1)])
    row, _ = knowledge._add_document(
        db_session,
        {**record(1), "park_id": None, "state": "active"},
        created_by=seed_admin.id if operator_action == "user_owned" else None,
        stable_source=True,
    )
    db_session.commit()
    if operator_action == "edit":
        row.content = "Operator-owned repair note"
        row.revision += 1
        db_session.commit()
    elif operator_action == "delete":
        knowledge.remove(db_session, row)
    protected = (row.title, row.content, row.kind, row.state, row.revision)

    assert bundle.step(db_session, test_settings)["state"] == "ready"
    assert db_session.get(AIBundleDocument, record(1)["source_ref"]) is None
    write_bundle(
        tmp_path,
        [record(1, kind="manual", title="Second package", content="Second package content")],
    )
    assert bundle.step(db_session, test_settings)["state"] == "ready"
    db_session.refresh(row)
    assert (row.title, row.content, row.kind, row.state, row.revision) == protected
    assert db_session.get(AIBundleDocument, record(1)["source_ref"]) is None


def test_matching_legacy_bundle_document_is_adopted_and_updated(
    db_session, test_settings, tmp_path
):
    setup_bundle(tmp_path, test_settings, [record(1)])
    row, _ = knowledge._add_document(
        db_session,
        {**record(1), "park_id": None, "state": "active"},
        created_by=None,
        stable_source=True,
    )
    db_session.commit()

    assert bundle.step(db_session, test_settings)["state"] == "ready"
    assert db_session.get(AIBundleDocument, record(1)["source_ref"]).document_id == row.id
    write_bundle(tmp_path, [record(1, content="Updated managed package")])
    assert bundle.step(db_session, test_settings)["state"] == "ready"
    db_session.refresh(row)
    assert row.content == "Updated managed package"
    assert row.revision == 2


@pytest.mark.parametrize("field,value", [("title", " \n\t"), ("content", "\x00")])
def test_redacted_empty_record_rejects_whole_bundle_before_first_batch(
    db_session, test_settings, tmp_path, field, value
):
    setup_bundle(tmp_path, test_settings, [record(1), record(2, **{field: value})])
    result = bundle.step(db_session, test_settings, batch_size=1)
    assert result["state"] == "failed" and result["error"] == "ai_bundle_invalid"
    assert count(db_session) == 0


def test_disable_committed_during_inspection_prevents_import(
    db_session, db_engine, test_settings, tmp_path, monkeypatch
):
    setup_bundle(tmp_path, test_settings, [record(1)])
    original = bundle.inspect

    def disable_then_inspect(settings):
        with Session(db_engine) as other:
            other.add(AIConfig(id=1, enabled=False))
            other.commit()
        return original(settings)

    monkeypatch.setattr(bundle, "inspect", disable_then_inspect)
    assert bundle.step(db_session, test_settings)["state"] == "paused"
    assert count(db_session) == 0


def test_part_replaced_after_validation_cannot_commit_unverified_document(
    db_session, test_settings, tmp_path, monkeypatch
):
    setup_bundle(tmp_path, test_settings, [record(1)])
    original = bundle.inspect

    def replace_after_inspection(settings):
        result = original(settings)
        write_bundle(tmp_path, [record(9)])
        return result

    monkeypatch.setattr(bundle, "inspect", replace_after_inspection)
    with pytest.raises(ValueError, match="ai_bundle_invalid"):
        bundle.step(db_session, test_settings)
    assert count(db_session) == 0


def test_corrupt_part_is_rejected_before_any_document_is_imported(
    db_session, test_settings, tmp_path
):
    root = setup_bundle(tmp_path, test_settings, [record(1), record(2)])
    (root / "seed.jsonl").write_text((root / "seed.jsonl").read_text() + "garbage")
    assert bundle.step(db_session, test_settings)["state"] == "failed"
    assert count(db_session) == 0
    write_bundle(tmp_path, [record(1)])
    assert bundle.step(db_session, test_settings)["state"] == "ready"


@pytest.mark.parametrize(
    "bad", [record(2, kind="evil"), record(2, park_id=3), record(2, source_ref="/private/source")]
)
def test_bad_late_record_rejects_whole_bundle(db_session, test_settings, tmp_path, bad):
    setup_bundle(tmp_path, test_settings, [record(1), bad])
    result = bundle.step(db_session, test_settings, batch_size=1)
    assert result["state"] == "failed" and result["error"] == "ai_bundle_invalid"
    assert count(db_session) == 0


def test_manifest_path_cannot_escape_bundle(db_session, test_settings, tmp_path):
    root = setup_bundle(tmp_path, test_settings, [record(1)])
    path = root / "manifest.json"
    value = json.loads(path.read_text())
    value["parts"][0]["path"] = "../outside.jsonl"
    path.write_text(json.dumps(value))
    assert bundle.step(db_session, test_settings)["state"] == "failed"
    assert count(db_session) == 0


def test_batch_failure_rolls_back_progress_and_documents(
    db_session, test_settings, tmp_path, monkeypatch
):
    setup_bundle(tmp_path, test_settings, [record(1), record(2)])
    original = knowledge.reindex
    calls = 0

    def failing(db, row):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic interrupted import")
        return original(db, row)

    monkeypatch.setattr(knowledge, "reindex", failing)
    with pytest.raises(RuntimeError, match="synthetic"):
        bundle.step(db_session, test_settings)
    assert count(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(AIBundleDocument)) == 0
    monkeypatch.setattr(knowledge, "reindex", original)
    assert bundle.step(db_session, test_settings)["processed"] == 2
    assert count(db_session) == 2


def test_worker_tick_prepares_bundle_and_status_exposes_progress(
    db_session, db_engine, test_settings, tmp_path, seed_admin, client
):
    from sqlalchemy.orm import sessionmaker

    from conftest import login_as
    from robopark_api.services.ai import jobs

    setup_bundle(tmp_path, test_settings, [record(i) for i in range(30)])
    login_as(client, "admin", "secret")
    assert jobs.tick(sessionmaker(bind=db_engine), test_settings, first=True)
    value = client.get("/ai/status").json()
    assert value["knowledge_bundle"]["state"] == "importing"
    assert value["knowledge_bundle"]["processed"] == 25
    assert jobs.tick(sessionmaker(bind=db_engine), test_settings)
    assert client.get("/ai/status").json()["knowledge_bundle"]["state"] == "ready"
