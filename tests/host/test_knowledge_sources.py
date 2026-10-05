import json

import pytest

from scripts.grounded_knowledge.sources import (
    build,
    copy_verified_original,
    digest,
    telegram_records,
    tracker_records,
)


def test_chat_keeps_short_answers_without_inventing_reply_links():
    rows = list(
        telegram_records(
            {
                "id": 1,
                "messages": [
                    {
                        "id": 10,
                        "type": "message",
                        "text": "Камера после замены работает?",
                    },
                    {
                        "id": 11,
                        "type": "message",
                        "text": ["Нет", {"text": "."}],
                        "reply_to_message_id": 10,
                    },
                    {"id": 12, "type": "message", "text": "Всё исправно"},
                    {
                        "id": 13,
                        "type": "message",
                        "text": "Да",
                        "reply_to_message_id": 3,
                    },
                ],
            }
        )
    )
    assert rows[1]["text"] == "Нет."
    assert rows[1]["reply_to"] == "telegram:1:message:10"
    assert rows[2]["reply_to"] is None
    assert rows[3]["reply_missing"] is True
    other = list(
        telegram_records(
            {"id": 2, "messages": [{"id": 11, "type": "message", "text": "Да"}]}
        )
    )
    assert other[0]["source_ref"] != rows[1]["source_ref"]


def test_failed_tracker_comment_capture_is_not_silent_empty_evidence():
    with pytest.raises(ValueError, match="source_comments_missing"):
        list(
            tracker_records(
                {
                    "key": "X-1",
                    "sections": {
                        "issue": {"ok": True, "value": {"description": "description"}},
                        "comments": {"ok": False, "value": []},
                    },
                }
            )
        )


def test_forum_topic_membership_does_not_become_a_question_answer_link():
    rows = list(
        telegram_records(
            {
                "id": 7,
                "messages": [
                    {
                        "id": 2,
                        "type": "service",
                        "action": "topic_created",
                        "title": "Ремонт",
                    },
                    {
                        "id": 10,
                        "type": "message",
                        "text": "Вопрос А",
                        "reply_to_message_id": 2,
                    },
                    {
                        "id": 11,
                        "type": "message",
                        "text": "Соседний вопрос Б",
                        "reply_to_message_id": 2,
                    },
                    {
                        "id": 12,
                        "type": "message",
                        "text": "Ответ А",
                        "reply_to_message_id": 10,
                    },
                ],
            }
        )
    )
    assert rows[0]["source_ref"] == "telegram:7:topic:2"
    assert rows[1]["reply_to"] is None and rows[2]["reply_to"] is None
    assert rows[1]["topic_ref"] == rows[2]["topic_ref"] == "telegram:7:topic:2"
    assert rows[3]["reply_to"] == "telegram:7:message:10"


def test_sourcebook_conflicting_ids_fail_closed_and_never_overwrite_input(tmp_path):
    source = tmp_path / "originals"
    source.mkdir()
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "source-map.private.json").write_text("{}")
    (prepared / "seed.jsonl").write_text("")
    for i, text in enumerate(("Исправно", "Неисправно")):
        folder = source / f"ChatExport{i}"
        folder.mkdir()
        (folder / "result.json").write_text(
            json.dumps(
                {"id": 1, "messages": [{"id": 1, "type": "message", "text": text}]}
            )
        )
    with pytest.raises(ValueError, match="source_duplicate_conflict"):
        build(source, prepared, tmp_path / "ledger")
    with pytest.raises(ValueError, match="overlaps"):
        build(source, prepared, source / "ledger")


def test_same_text_with_different_reply_context_is_a_conflict(tmp_path):
    source, prepared = tmp_path / "originals", tmp_path / "prepared"
    source.mkdir()
    prepared.mkdir()
    (prepared / "source-map.private.json").write_text("{}")
    for index, parent in enumerate((10, 20)):
        folder = source / f"ChatExport{index}"
        folder.mkdir()
        (folder / "result.json").write_text(
            json.dumps(
                {
                    "id": 1,
                    "messages": [
                        {
                            "id": 42,
                            "type": "message",
                            "text": "Да",
                            "reply_to_message_id": parent,
                        }
                    ],
                }
            )
        )
    with pytest.raises(ValueError, match="source_duplicate_conflict"):
        build(source, prepared, tmp_path / "ledger")


def test_prepared_document_text_is_never_evidence(tmp_path):
    from scripts.build_knowledge_seed import source_id

    source, prepared = tmp_path / "originals", tmp_path / "prepared"
    source.mkdir()
    prepared.mkdir()
    relative = __import__("pathlib").Path("codes.json")
    (source / relative).write_text(
        json.dumps([{"text": "BD-10 - Потертость/Царапины"}])
    )
    ref = source_id(relative)
    (prepared / "source-map.private.json").write_text(
        json.dumps({ref: {"relative_path": str(relative)}})
    )
    (prepared / "seed.jsonl").write_text(
        json.dumps(
            {
                "source_ref": ref + "#record-000001",
                "kind": "note",
                "content": "ВЫДУМАННЫЙ РЕМОНТ",
            }
        )
    )
    build(source, prepared, tmp_path / "ledger")
    text = (tmp_path / "ledger/sources.jsonl").read_text()
    assert "ВЫДУМАННЫЙ РЕМОНТ" not in text
    assert "BD-10" in text


def test_late_sourcebook_failure_leaves_no_output_and_retry_succeeds(tmp_path):
    source, prepared = tmp_path / "originals", tmp_path / "prepared"
    records = source / "tracker_year_all_parks/records"
    records.mkdir(parents=True)
    prepared.mkdir()
    (prepared / "source-map.private.json").write_text("{}")
    record = records / "one.json"
    record.write_text(
        json.dumps(
            {
                "key": "X-1",
                "sections": {
                    "issue": {
                        "ok": True,
                        "value": {"summary": "Камера", "description": "Нет картинки"},
                    },
                    "comments": {"ok": False, "value": []},
                },
            }
        )
    )
    output = tmp_path / "ledger"
    with pytest.raises(ValueError, match="sourcebook_incomplete"):
        build(source, prepared, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".ledger.tmp-*"))

    value = json.loads(record.read_text())
    value["sections"]["comments"] = {"ok": True, "value": []}
    record.write_text(json.dumps(value))
    report = build(source, prepared, output)
    assert report["tracker_issues"] == 1
    assert (output / "sources.jsonl").is_file()


def test_sourcebook_never_overwrites_existing_output(tmp_path):
    source, prepared = tmp_path / "originals", tmp_path / "prepared"
    source.mkdir()
    prepared.mkdir()
    (prepared / "source-map.private.json").write_text("{}")
    output = tmp_path / "ledger"
    output.mkdir()
    marker = output / "keep"
    marker.write_text("existing")
    with pytest.raises(FileExistsError):
        build(source, prepared, output)
    assert marker.read_text() == "existing"


def test_verified_copy_rejects_symlinked_parent_and_parent_escape(tmp_path):
    originals, outside = tmp_path / "originals", tmp_path / "outside"
    originals.mkdir()
    outside.mkdir()
    payload = outside / "manual.pdf"
    payload.write_bytes(b"outside")
    (originals / "linked").symlink_to(outside, target_is_directory=True)
    target = tmp_path / "copy.pdf"

    with pytest.raises(ValueError, match="grounding_original_path_invalid"):
        copy_verified_original(
            originals, "linked/manual.pdf", target, digest(payload.read_bytes())
        )
    with pytest.raises(ValueError, match="grounding_original_path_invalid"):
        copy_verified_original(
            originals, "../outside/manual.pdf", target, digest(payload.read_bytes())
        )
    assert not target.exists()
