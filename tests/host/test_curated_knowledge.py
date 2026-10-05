"""New exports retain semantics; private RAG excludes unreviewed dialogue dumps."""

import json

import pytest

from scripts.build_knowledge_seed import Builder
from scripts.build_private_knowledge import build_bundle
from scripts.build_repair_knowledge import assemble, build, ready_reference


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_full_tracker_envelope_is_a_ticket_not_a_generic_json_dump(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    (source / "record.json").write_text(
        json.dumps(
            {
                "key": "TEST-1",
                "sections": {
                    "issue": {
                        "ok": True,
                        "value": {
                            "summary": "Камера не работает",
                            "components": [{"display": "ROBOT_CAMERAS"}],
                            "custom--theDefectCode": "EL-02",
                            "solutionMethod": "CHANGE",
                        },
                    },
                    "comments": {
                        "ok": True,
                        "value": [
                            {
                                "createdBy": {"display": "Иван Петров"},
                                "text": "Иван Петров заменил кабель",
                            }
                        ],
                    },
                },
            }
        )
    )
    builder = Builder(source, output, 12000, 64000)
    report = builder.build()
    docs = rows(output / report["parts"][0]["path"])
    assert len(docs) == 1 and docs[0]["kind"] == "ticket"
    assert "Компонент: ROBOT_CAMERAS" in docs[0]["content"]
    assert "Код дефекта: EL-02" in docs[0]["content"]
    assert (
        "Зафиксированное действие: [имя удалено] заменил кабель" in docs[0]["content"]
    )
    assert "Иван Петров" not in docs[0]["content"]
    assert report["flagged_by_reason"]["tracker_section_unavailable"] == 1


@pytest.mark.parametrize(
    "issue", [None, [], {"ok": False, "value": {"summary": "do not ingest"}}]
)
def test_unavailable_tracker_issue_is_quarantined(tmp_path, issue):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    (source / "record.json").write_text(
        json.dumps({"key": "TEST-1", "sections": {"issue": issue}})
    )
    report = Builder(source, output, 12000, 64000).build()
    assert report["documents"] == 0
    assert report["quarantined_by_reason"] == {"tracker_issue_unavailable": 1}


def test_excluded_tracker_directory_cannot_leak_back_as_generic_documents(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    tracker = source / "tracker"
    tracker.mkdir()
    (tracker / "raw.json").write_text('{"text": "private raw ticket"}')
    (source / "reference.md").write_text("# Independent reference\nA useful note")
    builder = Builder(source, output, 12000, 64000, excluded_directories=(tracker,))
    report = builder.build()
    assert report["documents"] == 1
    assert all(
        "tracker" not in item["relative_path"] for item in builder.source_map.values()
    )


def test_reference_policy_does_not_promote_skills_chats_or_incomplete_manuals(tmp_path):
    def reference(kind, title, suffix):
        return ready_reference(
            {"kind": kind, "title": title, "source_ref": "source:1"},
            {
                "source:1": {"relative_path": "file" + suffix},
            },
        )

    assert reference("manual", "Инструкция: Замена", ".pdf")
    assert reference("note", "Неполная инструкция: Замена", ".pdf")
    assert reference("note", "Справочник кодов: EL-02 — Не работает", ".json")
    assert not reference("note", "Справочник: Остатки склада", ".xlsx")
    assert not reference("manual", "Инструкция: SKILL", ".md")
    assert not reference("chat", "Ремонт", ".json")
    assert not reference("note", "Неполная инструкция: Замена", ".json")


def test_curated_replacement_does_not_restore_removed_public_tickets(tmp_path):
    key = b"k" * 32
    old = {
        "title": "Old ticket",
        "kind": "ticket",
        "content": "Old source",
        "source_ref": "public:repair-v1:" + "a" * 32,
    }
    new = {
        "title": "Repair card",
        "kind": "note",
        "content": "Observed evidence, not proof",
        "source_ref": "repair-card:v1:new",
    }
    private, public = tmp_path / "new.jsonl", tmp_path / "public.jsonl"
    private.write_text(json.dumps(new) + "\n")
    public.write_text(json.dumps(old) + "\n")
    result = build_bundle(
        private, public, key, tmp_path / "bundle", replace_public=True
    )
    assert result["documents"] == 1
    assert rows(tmp_path / "bundle/seed.jsonl")[0]["title"] == new["title"]
    fallback = build_bundle(private, public, key, tmp_path / "compatible")
    assert fallback["documents"] == 2


def test_output_must_not_overlap_private_source(tmp_path):
    source = tmp_path / "source"
    tracker = source / "tracker"
    tracker.mkdir(parents=True)
    with pytest.raises(ValueError, match="overlaps"):
        build(source, tracker, tracker / "generated")


def test_missing_offline_readers_fail_before_partial_corpus_is_created(
    tmp_path, monkeypatch
):
    from scripts import build_repair_knowledge

    source = tmp_path / "source"
    tracker = source / "tracker"
    tracker.mkdir(parents=True)
    monkeypatch.setattr(
        build_repair_knowledge.importlib.util, "find_spec", lambda _: None
    )
    with pytest.raises(ValueError, match="knowledge_readers_missing"):
        build(source, tracker, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_assembly_keeps_only_allowlisted_references_and_cards(tmp_path):
    evidence, tracker = tmp_path / "evidence", tmp_path / "tracker"
    evidence.mkdir()
    tracker.mkdir()
    records = [
        {
            "title": "Инструкция: Камера",
            "kind": "manual",
            "source_ref": "m",
            "content": "Manual",
        },
        {"title": "Thread", "kind": "chat", "source_ref": "c", "content": "Discussion"},
    ]
    (evidence / "seed.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    (evidence / "manifest.json").write_text(
        json.dumps({"parts": [{"path": "seed.jsonl"}]})
    )
    card = {
        "title": "Карточка опыта",
        "content": "Reported repairs",
        "kind": "note",
        "source_ref": "card",
    }
    (tracker / "rag-seed.jsonl").write_text(json.dumps(card) + "\n")
    result = assemble(
        evidence, tracker, tmp_path, {"m": {"relative_path": "manual.pdf"}}
    )
    assert result["documents"] == 2 and result["repair_cards"] == 1
    assert result["review_pool_by_kind"] == {"chat": 1}
    assert {r["kind"] for r in rows(tmp_path / "seed.jsonl")} == {"manual", "note"}


def test_duplicate_code_exports_merge_without_silencing_label_conflicts(tmp_path):
    evidence, tracker = tmp_path / "evidence", tmp_path / "tracker"
    evidence.mkdir()
    tracker.mkdir()
    records = [
        {
            "title": "Справочник кодов: BD-01",
            "kind": "note",
            "source_ref": f"ref{i}",
            "content": "Контекст документа: справочник\nНазвание: Export\n\nКод дефекта: "
            + value,
        }
        for i, value in enumerate(
            (
                "BD-01 - Вмятина\nОписание: Первый вариант определения",
                "BD-1 - Вмятина\nОписание: Первый вариант определения",
                "BD-01 - Повреждение\nОписание: Другое определение",
            )
        )
    ]
    (evidence / "seed.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    (evidence / "manifest.json").write_text(
        json.dumps({"parts": [{"path": "seed.jsonl"}]})
    )
    (tracker / "rag-seed.jsonl").write_text("")
    result = assemble(
        evidence,
        tracker,
        tmp_path,
        {f"ref{i}": {"relative_path": "codes.json"} for i in range(3)},
    )
    documents = rows(tmp_path / "seed.jsonl")
    assert len(documents) == 1
    assert documents[0]["source_ref"] == "reference-code:v1:bd-01"
    assert (
        "Вмятина" in documents[0]["content"]
        and "Повреждение" in documents[0]["content"]
    )
    assert "требуется сверка" in documents[0]["content"]
    assert result["reference_duplicates_merged"] == 2
    assert result["reference_label_conflicts"] == 1
    assert result["reference_definition_variants"] == 1
    assert documents[0]["content"].count("Первый вариант определения") == 1
    assert "Другое определение" in documents[0]["content"]
    assert json.loads((tmp_path / "reference-source-map.private.json").read_text()) == {
        "BD-01": ["ref0", "ref1", "ref2"]
    }
