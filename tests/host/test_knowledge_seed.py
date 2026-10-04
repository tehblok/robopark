import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build_knowledge_seed.py"


def run_builder(
    source: Path, output: Path, *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(output),
            *extra,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def load_documents(output: Path) -> list[dict]:
    manifest = json.loads((output / "manifest.json").read_text())
    documents = []
    for part in manifest["parts"]:
        documents.extend(
            json.loads(line)
            for line in (output / part["path"]).read_text().splitlines()
        )
    return documents


def test_builds_redacted_chunked_seed_and_manifest(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "manual.md").write_text(
        "# Настройка\n\n"
        + "Безопасная инструкция. " * 30
        + " admin@example.org +7 999 123-45-67 token=very-secret-value"
    )
    output = tmp_path / "seed"

    result = run_builder(
        source, output, "--chunk-chars", "180", "--part-max-bytes", "700"
    )

    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    documents = load_documents(output)
    assert len(documents) > 1
    assert len(manifest["parts"]) > 1
    assert manifest["documents"] == len(documents)
    assert manifest["coverage"][".md"]["processed"] == 1
    for document in documents:
        assert set(document) == {"title", "content", "kind", "source_ref"}
        assert document["kind"] == "note"
        assert len(document["content"]) <= 180
        assert document["source_ref"].startswith("source:")
        assert "manual.md" not in document["source_ref"]
    joined = "\n".join(document["content"] for document in documents)
    assert "admin@example.org" not in joined
    assert "999 123" not in joined
    assert "very-secret-value" not in joined
    assert manifest["redactions"]["email"] == 1
    assert all(part["bytes"] <= 700 for part in manifest["parts"])


def test_extracts_telegram_threads_and_tickets_without_identity(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "result.json").write_text(
        json.dumps(
            {
                "name": "Личный чат",
                "type": "personal_chat",
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "from": "Иван Петров",
                        "text": "Иван Петров: проверьте питание",
                    },
                    {
                        "id": 2,
                        "type": "message",
                        "from": "Мария",
                        "reply_to_message_id": 1,
                        "text": [{"type": "plain", "text": "Готово, +7 900 111-22-33"}],
                    },
                ],
            },
            ensure_ascii=False,
        )
    )
    (source / "tickets.json").write_text(
        json.dumps(
            {
                "tickets": [
                    {
                        "key": "RP-42",
                        "summary": "Не запускается",
                        "issue": {
                            "description": "Проверить контроллер",
                            "solutionMethod": "Сбросить питание",
                        },
                        "comments": [
                            {
                                "createdBy": {"display": "Сергей"},
                                "text": "Сергей заменил кабель",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    output = tmp_path / "seed"

    result = run_builder(source, output)

    assert result.returncode == 0, result.stderr
    documents = load_documents(output)
    chats = [doc for doc in documents if doc["kind"] == "chat"]
    tickets = [doc for doc in documents if doc["kind"] == "ticket"]
    assert chats and tickets
    chat_text = "\n".join(doc["content"] for doc in chats)
    assert "проверьте питание" in chat_text and "Готово" in chat_text
    assert "Иван Петров" not in chat_text and "Мария" not in chat_text
    assert "900 111" not in chat_text
    ticket_text = "\n".join(doc["content"] for doc in tickets)
    assert (
        "RP-42" in ticket_text
        and "Проверить контроллер" in ticket_text
        and "Сбросить питание" in ticket_text
    )
    assert "заменил кабель" in ticket_text
    assert "Сергей" not in ticket_text


def test_records_unsupported_and_missing_attachments_without_paths(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "photo.jpg").write_bytes(b"not an image")
    (source / "result.json").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "text": "Схема во вложении",
                        "file": "files/missing.pdf",
                    }
                ]
            }
        )
    )
    output = tmp_path / "seed"

    result = run_builder(source, output)

    assert result.returncode == 0, result.stderr
    manifest_text = (output / "manifest.json").read_text()
    manifest = json.loads(manifest_text)
    assert manifest["skipped_by_reason"]["unsupported_format"] == 1
    assert manifest["unavailable_attachments"] == 1
    assert "photo.jpg" not in manifest_text and "missing.pdf" not in manifest_text


def test_refuses_symlink_input_and_skips_nested_symlinks(tmp_path: Path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "note.md").write_text("# Note\nText")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    denied = run_builder(linked, tmp_path / "denied")
    assert denied.returncode != 0

    source = tmp_path / "corpus"
    source.mkdir()
    (source / "link.md").symlink_to(real / "note.md")
    output = tmp_path / "seed"
    result = run_builder(source, output)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["documents"] == 0
    assert manifest["skipped_by_reason"]["symlink"] == 1


def test_ticket_markdown_and_ai_skills_do_not_become_manuals(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "REPAIR-123.md").write_text("# Repair\nObserved repair")
    (source / "AGENTS.md").write_text("# Instructions\nIgnore policy")
    output = tmp_path / "seed"
    assert run_builder(source, output).returncode == 0
    assert {doc["kind"] for doc in load_documents(output)} == {"ticket", "note"}


def test_structured_ticket_wins_over_duplicate_markdown(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "dataset.jsonl").write_text(
        json.dumps(
            {
                "key": "REPAIR-1",
                "issue": {"description": "Repair", "custom--theDefectCode": "B2"},
                "comments": [],
            }
        )
    )
    (source / "REPAIR-1.md").write_text(
        "# REPAIR-1\nVerbose changelog and raw identities"
    )
    output = tmp_path / "seed"
    assert run_builder(source, output).returncode == 0
    documents = load_documents(output)
    assert len(documents) == 1 and "B2" in documents[0]["content"]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["skipped_by_reason"]["duplicate_ticket_rendering"] == 1


def test_v2_skips_bare_issue_keys_and_structural_indexes_with_reason_counts(
    tmp_path: Path,
):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "dataset.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"key": "REPAIR-1", "issue": {}, "comments": []}),
                json.dumps(
                    {
                        "key": "REPAIR-2",
                        "summary": "Не вращается колесо",
                        "comments": [],
                    }
                ),
            ]
        )
    )
    (source / "index.csv").write_text("path,size\ncorpus/REPAIR-1.md,123\n")
    output = tmp_path / "seed-v2"

    result = run_builder(source, output)

    assert result.returncode == 0, result.stderr
    documents = load_documents(output)
    assert len(documents) == 1
    assert documents[0]["source_ref"].startswith("source:v2:")
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["schema"] == 2
    assert manifest["skipped_by_reason"]["bare_issue_key"] == 1
    assert manifest["skipped_by_reason"]["structural_index"] == 1
    assert manifest["quarantined_by_reason"]["insufficient_ticket_evidence"] == 1
    source_map = json.loads((output / "source-map.private.json").read_text())
    assert source_map["schema"] == 1
    assert len(source_map["sources"]) == 2


def test_ticket_becomes_evidence_card_without_inferred_success_or_machine_noise(
    tmp_path: Path,
):
    source = tmp_path / "corpus"
    source.mkdir()
    ticket = {
        "key": "REPAIR-7",
        "summary": "Колесо не вращается",
        "issue": {
            "description": "Ошибка WH-02, мотор не запускается",
            "components": [{"display": "Мотор-колесо"}],
            "custom--theDefectCode": "WH-02 - короткое замыкание",
            "solutionMethod": "Заменили кабель 2 шт.; питание не включать до проверки",
            "status": {"display": "Закрыт"},
        },
        "comments": [
            {
                "text": "Проверили сопротивление: 4 Ом. После замены тест движения пройден."
            },
            {
                "text": 'Статус: В работе → Закрыт; SLA: {"id": "secret"}; https://tracker.invalid/REPAIR-7'
            },
        ],
    }
    (source / "dataset.jsonl").write_text(json.dumps(ticket, ensure_ascii=False))
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    document = load_documents(output)[0]
    assert document["title"] == "Ремонт REPAIR-7: Колесо не вращается"
    assert "Симптом: Ошибка WH-02, мотор не запускается" in document["content"]
    assert "Компонент: Мотор-колесо" in document["content"]
    assert "Код дефекта: WH-02 - короткое замыкание" in document["content"]
    assert (
        "Указанный метод решения: Заменили кабель 2 шт.; питание не включать до проверки"
        in document["content"]
    )
    assert (
        "Запись проверки: Проверили сопротивление: 4 Ом. После замены тест движения пройден."
        in document["content"]
    )
    assert "Причина:" not in document["content"]
    assert "Результат: успешно" not in document["content"]
    assert "SLA" not in document["content"] and "https://" not in document["content"]


def _write_test_xlsx(path: Path) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
      <Default Extension="xml" ContentType="application/xml"/>
      <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
      <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
    </Types>"""
    workbook = """<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Соответствия" sheetId="1" r:id="rId1"/></sheets></workbook>"""
    rels = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>"""
    sheet = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>Ошибка</t></is></c><c r="B1" t="inlineStr"><is><t>Компонент</t></is></c><c r="C1" t="inlineStr"><is><t>Код дефекта</t></is></c><c r="D1" t="inlineStr"><is><t>Количество</t></is></c></row>
      <row r="2"><c r="A2" t="inlineStr"><is><t>Не вращается</t></is></c><c r="B2" t="inlineStr"><is><t>Мотор-колесо</t></is></c><c r="C2" t="inlineStr"><is><t>WH-02</t></is></c><c r="D2"><v>2</v></c></row>
      <row r="3"><c r="A3" t="inlineStr"><is><t>Проверить формулу</t></is></c><c r="D3"><f>1+1</f><v>2</v></c></row>
    </sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def test_reads_xlsx_mappings_without_evaluating_formulas(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    _write_test_xlsx(source / "Соответствия.xlsx")
    output = tmp_path / "seed-v2"

    result = run_builder(source, output)

    assert result.returncode == 0, result.stderr
    docs = load_documents(output)
    assert len(docs) == 2
    joined = "\n".join(doc["content"] for doc in docs)
    assert "Ошибка: Не вращается" in joined
    assert "Компонент: Мотор-колесо" in joined
    assert "Код дефекта: WH-02" in joined
    assert "Количество: 2" in joined
    assert "1+1" not in joined
    assert "формула не вычислялась" in joined
    assert all(
        doc["kind"] == "note" and doc["title"].startswith("Справочник:") for doc in docs
    )


def test_segments_chat_cases_by_time_and_discards_ack_only_groups(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    messages = {
        "messages": [
            {
                "id": 1,
                "type": "message",
                "date": "2026-01-01T10:00:00",
                "from": "A",
                "text": "Робот R7 не заряжается, ошибка CH-4",
            },
            {
                "id": 2,
                "type": "message",
                "date": "2026-01-01T10:05:00",
                "from": "B",
                "reply_to_message_id": 1,
                "text": "@mechanic Проверь разъём зарядки и напряжение",
            },
            {
                "id": 3,
                "type": "message",
                "date": "2026-01-01T10:06:00",
                "from": "A",
                "reply_to_message_id": 1,
                "text": "ок",
            },
            {
                "id": 4,
                "type": "message",
                "date": "2026-01-03T11:00:00",
                "from": "A",
                "text": "Лидар L2 не видит препятствия",
            },
            {
                "id": 5,
                "type": "message",
                "date": "2026-01-03T11:04:00",
                "from": "B",
                "reply_to_message_id": 4,
                "text": "Очистили стекло, диагностика снова видит 12 точек",
            },
            {
                "id": 6,
                "type": "message",
                "date": "2026-01-04T12:00:00",
                "from": "A",
                "text": "спасибо",
            },
        ]
    }
    (source / "result.json").write_text(json.dumps(messages, ensure_ascii=False))
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    docs = [doc for doc in load_documents(output) if doc["kind"] == "chat"]
    assert len(docs) == 2
    assert all(doc["title"].startswith("Случай из переписки:") for doc in docs)
    joined = "\n".join(doc["content"] for doc in docs)
    assert "не заряжается" in joined and "Лидар" in joined
    assert "@mechanic" not in joined
    assert "\n\nок" not in joined and "спасибо" not in joined
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["skipped_by_reason"]["chat_acknowledgement"] == 2


def test_docx_catalog_is_note_and_missing_visuals_are_flagged(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    document_xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Каталог запасных частей</w:t></w:r></w:p>
      <w:p><w:r><w:t>Артикул WH-001 — мотор-колесо, 2 шт.</w:t></w:r></w:p>
      <w:p><w:r><w:t>См. изображение ниже для расположения разъёма.</w:t></w:r></w:p>
    </w:body></w:document>"""
    with zipfile.ZipFile(source / "catalog.docx", "w") as archive:
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/media/image1.png", b"not-read")
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    document = load_documents(output)[0]
    assert document["kind"] == "note"
    assert document["title"].startswith("Справочник:")
    assert "2 шт." in document["content"]
    assert "[В исходном документе есть визуальные материалы" in document["content"]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["flagged_by_reason"]["visual_context_present"] == 1


def test_deduplicates_repeated_evidence_and_records_reason(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    content = "# Замена колеса\n\nОтключить питание. Заменить колесо. Проверить свободное вращение."
    (source / "copy-a.md").write_text(content)
    (source / "copy-b.md").write_text(content)
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    assert len(load_documents(output)) == 1
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["skipped_by_reason"]["duplicate_content"] == 1


def test_skips_issue_key_lists_and_machine_only_chat_messages(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "keys.json").write_text(json.dumps(["REPAIR-1", "REPAIR-2"]))
    (source / "result.json").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "from": "Сервисный бот",
                        "text": 'Статус: В работе → Закрыт; SLA: {"id": "42"}',
                    },
                    {
                        "id": 2,
                        "type": "message",
                        "from": "Робот Уведомления",
                        "text": "⛔️ Поступила новая задача!\n[a1024] mechanical_problem\nСсылка на тикет: https://tracker.invalid/X-1",
                    },
                ]
            },
            ensure_ascii=False,
        )
    )
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    assert load_documents(output) == []
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["skipped_by_reason"]["bare_issue_key"] == 2
    assert manifest["skipped_by_reason"]["chat_service_message"] == 2


def test_quarantines_isolated_chat_question_without_answer(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "result.json").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "from": "Оператор",
                        "text": "Кто-нибудь знает, почему робот не заряжается?",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    assert load_documents(output) == []
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["quarantined_by_reason"]["isolated_chat_without_answer"] == 1


def test_normalizes_defect_form_rows_and_deduplicates_internal_ids(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    rows = [
        [
            ["ID", "1001"],
            ["Выберите код дефекта", "WH-02 - Короткое замыкание"],
            ["Кто заполнял?", "user-a"],
        ],
        [
            ["ID", "1002"],
            ["Выберите код дефекта", "WH-02 - Короткое замыкание"],
            ["Кто заполнял?", "user-b"],
        ],
    ]
    (source / "defects.json").write_text(json.dumps(rows, ensure_ascii=False))
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    documents = load_documents(output)
    assert len(documents) == 1
    assert documents[0]["title"] == "Справочник кодов: WH-02 — Короткое замыкание"
    assert "Код дефекта: WH-02 - Короткое замыкание" in documents[0]["content"]
    assert (
        "1001" not in documents[0]["content"]
        and "user-a" not in documents[0]["content"]
    )


@pytest.mark.parametrize(
    "heading",
    [
        "Информация об инструкции:",
        "Техника безопасности {#tehnika-bezopasnosti23}",
        "wiki.example.test/robot/repair",
    ],
)
def test_manual_uses_operation_filename_when_first_heading_is_generic(
    tmp_path: Path, heading
):
    source = tmp_path / "corpus"
    source.mkdir()
    document_xml = f"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>{heading}</w:t></w:r></w:p>
      <w:p><w:r><w:t>Инструкция по ремонту:</w:t></w:r></w:p>
      <w:p><w:r><w:t>Отключить питание перед заменой.</w:t></w:r></w:p>
    </w:body></w:document>"""
    with zipfile.ZipFile(source / "Замена_мотор-колеса_Ви.docx", "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    document = load_documents(output)[0]
    assert document["title"] == "Инструкция: Замена мотор-колеса"


def test_manual_uses_operation_heading_when_export_filename_is_generic(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    document_xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Информация об инструкции</w:t></w:r></w:p>
      <w:p><w:r><w:t>Снятие крышки грузового отсека</w:t></w:r></w:p>
      <w:p><w:r><w:t>1. Отключить питание.</w:t></w:r></w:p>
    </w:body></w:document>"""
    with zipfile.ZipFile(source / "wiki.example.test.docx", "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    document = load_documents(output)[0]
    assert document["title"] == "Инструкция: Снятие крышки грузового отсека"


def test_manual_uses_meaningful_filename_instead_of_generic_materials_heading(
    tmp_path: Path,
):
    source = tmp_path / "corpus"
    source.mkdir()
    document_xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Материалы</w:t></w:r></w:p>
      <w:p><w:r><w:t>Термоусадочная трубка — 15 мм</w:t></w:r></w:p>
      <w:p><w:r><w:t>Порядок действий: отключить питание.</w:t></w:r></w:p>
    </w:body></w:document>"""
    with zipfile.ZipFile(source / "Инструкция_по_ремонту_кабеля.docx", "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    document = load_documents(output)[0]
    assert document["title"] == "Инструкция: Инструкция по ремонту кабеля"


def test_parts_document_is_a_catalog_note_even_without_catalog_word(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    document_xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>5512119XNU60C Левая задняя накладка</w:t></w:r></w:p>
      <w:p><w:r><w:t>5512120XNU60C Правая задняя накладка</w:t></w:r></w:p>
    </w:body></w:document>"""
    with zipfile.ZipFile(source / "детали 1.docx", "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    document = load_documents(output)[0]
    assert document["kind"] == "note"
    assert document["title"].startswith("Справочник:")


def test_splits_reply_thread_when_robot_changes(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "result.json").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "date": "2026-01-01T10:00:00",
                        "from": "A",
                        "text": "a1001 не заряжается, ошибка CH-4",
                    },
                    {
                        "id": 2,
                        "type": "message",
                        "date": "2026-01-01T10:05:00",
                        "from": "B",
                        "reply_to_message_id": 1,
                        "text": "Для a1001 проверили разъём и заменили кабель",
                    },
                    {
                        "id": 3,
                        "type": "message",
                        "date": "2026-01-01T10:10:00",
                        "from": "A",
                        "reply_to_message_id": 1,
                        "text": "a2002 не видит лидар, проверьте стекло",
                    },
                    {
                        "id": 4,
                        "type": "message",
                        "date": "2026-01-01T10:15:00",
                        "from": "B",
                        "reply_to_message_id": 1,
                        "text": "Для a2002 очистили стекло, лидар снова видит точки",
                    },
                ]
            },
            ensure_ascii=False,
        )
    )
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    documents = load_documents(output)
    assert len(documents) == 2
    assert all(
        not ("a1001" in doc["content"] and "a2002" in doc["content"])
        for doc in documents
    )


def test_ticket_diagnostics_without_outcome_is_action_not_success_check(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    ticket = {
        "key": "REPAIR-23",
        "summary": "[a1001] suspension by user",
        "issue": {
            "description": "**SUF**: служебный шаблон\n**Comment:** Повреждены крепления\nПриоритет - Блокер\n<[Подробное описание]>\n<{Файлы\n}>"
        },
        "comments": [{"text": "Замена креплений, диагностика узла"}],
    }
    (source / "dataset.jsonl").write_text(json.dumps(ticket, ensure_ascii=False))
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    document = load_documents(output)[0]
    assert "Зафиксированное действие:" not in document["content"]
    assert "Запись проверки:" in document["content"]
    assert "действие по ремонту не зафиксировано" in document["content"]
    assert "SUF" not in document["content"] and "Приоритет" not in document["content"]
    assert document["title"] == "Ремонт REPAIR-23: Повреждены крепления"
    assert "Подробное описание" not in document["content"]


def test_table_omits_opaque_ids_owners_and_anonymous_columns(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "parts.csv").write_text(
        "Артикул,Компонент,BOM owner,opaque TC UID,Поле 25,Количество\nWH-01,Мотор-колесо,user,opaque-42,photoURL,2\n"
    )
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    content = load_documents(output)[0]["content"]
    assert (
        "Артикул: WH-01" in content
        and "Компонент: Мотор-колесо" in content
        and "Количество: 2" in content
    )
    assert "owner" not in content.casefold() and "uid" not in content.casefold()
    assert "Поле 25" not in content and "photoURL" not in content


def test_ticket_keeps_negative_recommendations_and_unknown_comments_neutral(
    tmp_path: Path,
):
    source = tmp_path / "corpus"
    source.mkdir()
    ticket = {
        "key": "REPAIR-99",
        "summary": "Робот дёргается",
        "status": "Закрыт",
        "comments": [
            {"text": "Нужно заменить кабель и проверьте, работает ли привод"},
            {"text": "Привод не работает после запуска"},
            {"text": "Контакт отошёл, робот дёргается под нагрузкой"},
        ],
    }
    (source / "dataset.jsonl").write_text(json.dumps(ticket, ensure_ascii=False))
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    content = load_documents(output)[0]["content"]
    assert "Зафиксированное действие:" not in content
    assert (
        "Запись проверки: Нужно заменить кабель и проверьте, работает ли привод; Привод не работает после запуска"
        in content
    )
    assert "Записи участников (неподтверждённые):" in content
    assert "Контакт отошёл, робот дёргается под нагрузкой" in content
    assert "закрытие тикета само по себе не подтверждает успешность ремонта" in content
    assert "действие по ремонту не зафиксировано" in content


def test_solution_method_is_taxonomy_not_completed_work(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    ticket = {
        "key": "REPAIR-55",
        "summary": "Датчик не отвечает",
        "issue": {"solutionMethod": "DIAG"},
    }
    (source / "dataset.jsonl").write_text(json.dumps(ticket, ensure_ascii=False))
    output = tmp_path / "seed-v2"
    assert run_builder(source, output).returncode == 0
    content = load_documents(output)[0]["content"]
    assert "Зафиксированное действие:" not in content
    assert "Указанный метод решения: DIAG" in content
    assert "действие по ремонту не зафиксировано" in content


def test_manual_removes_author_and_wiki_footer_without_losing_steps(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    document_xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Замена колеса</w:t></w:r></w:p>
      <w:p><w:r><w:t>Автор инструкции: Иван Иванов</w:t></w:r></w:p>
      <w:p><w:r><w:t>1. Отключите питание.</w:t></w:r></w:p>
      <w:p><w:r><w:t>О сервисе Сообщество Конфиденциаль © 2026</w:t></w:r></w:p>
      <w:p><w:r><w:t>Обновлено 7 сентября 2026, 17:46 Иван Иванов</w:t></w:r></w:p>
    </w:body></w:document>"""
    with zipfile.ZipFile(source / "Замена колеса.docx", "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    content = load_documents(output)[0]["content"]
    assert "1. Отключите питание." in content
    assert "Иван Иванов" not in content
    assert "О сервисе" not in content and "Обновлено" not in content


def test_ticket_removes_tracker_quote_history_and_handles_before_classification(
    tmp_path: Path,
):
    source = tmp_path / "corpus"
    source.mkdir()
    ticket = {
        "key": "REPAIR-201",
        "summary": "Не включается контроллер",
        "comments": [
            {
                "text": (
                    "@mechanic Новый осмотр: контакт отошёл.\n"
                    "> [В ответ на](https://tracker.invalid/comment)\n"
                    "> Ранее заменили контроллер и проверили питание.\n"
                    '{% cut "Предыдущие сообщения" %}\n'
                    "@olduser Старый кабель заменили.\n"
                    "{% endcut %}"
                )
            }
        ],
    }
    (source / "dataset.jsonl").write_text(json.dumps(ticket, ensure_ascii=False))
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    content = load_documents(output)[0]["content"]
    assert "Новый осмотр: контакт отошёл" in content
    assert "Ранее заменили" not in content and "Старый кабель" not in content
    assert "Зафиксированное действие:" not in content
    assert "@mechanic" not in content and "@olduser" not in content


def test_dedupe_keeps_same_text_for_distinct_document_contexts(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    document_xml = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Информация об инструкции</w:t></w:r></w:p>
      <w:p><w:r><w:t>Отключить питание. Проверить разъём XC1.</w:t></w:r></w:p>
    </w:body></w:document>"""
    for name in ("Замена контроллера.docx", "Ремонт жгута.docx"):
        with zipfile.ZipFile(source / name, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
    (source / "note.md").write_text(
        "Информация об инструкции\n\nОтключить питание. Проверить разъём XC1."
    )
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    documents = load_documents(output)
    assert len(documents) == 3
    assert {doc["kind"] for doc in documents} == {"manual", "note"}
    assert any(
        "Название: Инструкция: Замена контроллера" in doc["content"]
        for doc in documents
    )
    assert any(
        "Название: Инструкция: Ремонт жгута" in doc["content"] for doc in documents
    )


def test_image_only_document_is_incomplete_note_while_embedded_visual_is_flagged_manual(
    tmp_path: Path,
):
    source = tmp_path / "corpus"
    source.mkdir()
    with zipfile.ZipFile(source / "Схема подключения.docx", "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        )
        archive.writestr("word/media/image1.png", b"pixels")
    with zipfile.ZipFile(source / "Замена датчика.docx", "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Замена датчика</w:t></w:r></w:p><w:p><w:r><w:t>Отключить питание и снять разъём.</w:t></w:r></w:p></w:body></w:document>',
        )
        archive.writestr("word/media/image1.png", b"pixels")
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    documents = load_documents(output)
    incomplete = next(
        doc for doc in documents if doc["title"].startswith("Неполная инструкция:")
    )
    complete = next(
        doc for doc in documents if doc["title"] == "Инструкция: Замена датчика"
    )
    assert (
        incomplete["kind"] == "note"
        and "текст не извлечён" in incomplete["content"].casefold()
    )
    assert (
        complete["kind"] == "manual" and "визуальные материалы" in complete["content"]
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["flagged_by_reason"]["image_only_document"] == 1
    assert manifest["flagged_by_reason"]["visual_context_present"] == 1
    assert manifest["quarantined_by_reason"] == {}


def test_input_fingerprint_hashes_bytes_not_only_path_and_size(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "same.md").write_text("AAAA")
    (second / "same.md").write_text("BBBB")
    out_first = tmp_path / "out-first"
    out_second = tmp_path / "out-second"

    assert run_builder(first, out_first).returncode == 0
    assert run_builder(second, out_second).returncode == 0
    fingerprint_first = json.loads((out_first / "manifest.json").read_text())[
        "input_fingerprint"
    ]
    fingerprint_second = json.loads((out_second / "manifest.json").read_text())[
        "input_fingerprint"
    ]
    assert fingerprint_first != fingerprint_second


def test_source_map_accounts_for_every_source_disposition(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "photo.jpg").write_bytes(b"pixels")
    (source / "index.csv").write_text("path,size\na.md,1\n")
    (source / "dataset.jsonl").write_text(
        json.dumps(
            {
                "key": "REPAIR-1",
                "issue": {"description": "Не запускается"},
                "comments": [],
            }
        )
    )
    (source / "REPAIR-1.md").write_text("# REPAIR-1\nVerbose duplicate")
    (source / "a.md").write_text("# A\nОдинаковое содержание")
    (source / "b.md").write_text("# A\nОдинаковое содержание")
    (source / "broken.docx").write_bytes(b"not-a-zip")
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    source_map = json.loads((output / "source-map.private.json").read_text())["sources"]
    assert all(entry["evidence"] for entry in source_map.values())
    evidence = [event for entry in source_map.values() for event in entry["evidence"]]
    reasons = {event.get("reason") for event in evidence}
    assert {
        "unsupported_format",
        "structural_index",
        "duplicate_ticket_rendering",
        "parse_error",
        "duplicate_content",
    } <= reasons
    duplicates = [
        event
        for event in evidence
        if event.get("reason") in {"duplicate_ticket_rendering", "duplicate_content"}
    ]
    assert all(
        event.get("canonical_duplicate_ref", "").startswith("source:v2:")
        for event in duplicates
    )


def test_csv_is_bounded_before_materialization_and_records_truncation(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    rows = ["Код,Описание"] + [f"C-{index},Описание {index}" for index in range(20_005)]
    (source / "large.csv").write_text("\n".join(rows))
    output = tmp_path / "seed-v2"

    assert run_builder(source, output).returncode == 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["flagged_by_reason"]["table_truncated"] == 1
    assert manifest["documents"] == 20_000
