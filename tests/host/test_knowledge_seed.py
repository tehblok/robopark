import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build_knowledge_seed.py"


def run_builder(source: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output), *extra],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def load_documents(output: Path) -> list[dict]:
    manifest = json.loads((output / "manifest.json").read_text())
    documents = []
    for part in manifest["parts"]:
        documents.extend(json.loads(line) for line in (output / part["path"]).read_text().splitlines())
    return documents


def test_builds_redacted_chunked_seed_and_manifest(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "manual.md").write_text(
        "# Настройка\n\n" + "Безопасная инструкция. " * 30
        + " admin@example.org +7 999 123-45-67 token=very-secret-value"
    )
    output = tmp_path / "seed"

    result = run_builder(source, output, "--chunk-chars", "180", "--part-max-bytes", "700")

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
    (source / "result.json").write_text(json.dumps({
        "name": "Личный чат",
        "type": "personal_chat",
        "messages": [
            {"id": 1, "type": "message", "from": "Иван Петров", "text": "Иван Петров: проверьте питание"},
            {"id": 2, "type": "message", "from": "Мария", "reply_to_message_id": 1,
             "text": [{"type": "plain", "text": "Готово, +7 900 111-22-33"}]},
        ],
    }, ensure_ascii=False))
    (source / "tickets.json").write_text(json.dumps({"tickets": [{
        "key": "RP-42", "summary": "Не запускается",
        "issue": {"description": "Проверить контроллер", "solutionMethod": "Сбросить питание"},
        "comments": [{"createdBy": {"display": "Сергей"}, "text": "Сергей заменил кабель"}],
    }]}, ensure_ascii=False))
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
    assert "RP-42" in ticket_text and "Проверить контроллер" in ticket_text and "Сбросить питание" in ticket_text
    assert "заменил кабель" in ticket_text
    assert "Сергей" not in ticket_text


def test_records_unsupported_and_missing_attachments_without_paths(tmp_path: Path):
    source = tmp_path / "corpus"
    source.mkdir()
    (source / "photo.jpg").write_bytes(b"not an image")
    (source / "result.json").write_text(json.dumps({"messages": [{
        "id": 1, "type": "message", "text": "Схема во вложении", "file": "files/missing.pdf"
    }]}))
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
    (source / "dataset.jsonl").write_text(json.dumps({"key":"REPAIR-1", "issue":{"description":"Repair", "custom--theDefectCode":"B2"}, "comments":[]}))
    (source / "REPAIR-1.md").write_text("# REPAIR-1\nVerbose changelog and raw identities")
    output = tmp_path / "seed"
    assert run_builder(source, output).returncode == 0
    documents = load_documents(output)
    assert len(documents) == 1 and "B2" in documents[0]["content"]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["skipped_by_reason"]["duplicate_ticket_rendering"] == 1
