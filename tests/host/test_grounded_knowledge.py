import hashlib
import json

import pytest

import scripts.build_grounded_knowledge as grounded_builder
from scripts.grounded_knowledge.render import retrieval_units
from scripts.grounded_knowledge.verify import verify_articles


def article(*, quote="После замены изображение не появилось.", basis="observed"):
    return {
        "id": "camera",
        "evidence": {
            "roles": ["mechanic"],
            "questions": ["Что проверяли?"],
            "entities": ["камера"],
            "scope": "Исторический случай; не универсальная процедура.",
            "source_refs": ["message:1"],
            "citations": {"S1": ["message:1"]},
            "claims": [
                {
                    "claim": "Замена в этом случае не помогла.",
                    "basis": basis,
                    "sources": [
                        {
                            "source_ref": "message:1",
                            "locator": "/text",
                            "excerpt": quote,
                        }
                    ],
                }
            ],
        },
    }


def source():
    return {
        "message:1": {
            "kind": "conversation",
            "locator": "/text",
            "text": "После замены\nизображение не появилось.",
        }
    }


def test_exact_quote_preserves_negation_and_accepts_only_whitespace_difference():
    assert verify_articles([article()], source())["valid"]
    report = verify_articles(
        [article(quote="После замены изображение появилось.")], source()
    )
    assert not report["valid"]
    assert report["findings"][0]["reason"] == "quote_not_exact"


def test_editorial_advice_and_conversation_promoted_to_manual_are_rejected():
    assert any(
        f["reason"] == "unsupported_editorial_claim"
        for f in verify_articles([article(basis="editorial")], source())["findings"]
    )
    assert any(
        f["reason"] == "conversation_is_not_manual"
        for f in verify_articles([article(basis="manual")], source())["findings"]
    )


def test_citation_must_reach_a_verified_quote_and_exact_locator():
    value = article()
    value["evidence"]["citations"]["S2"] = ["message:2"]
    value["evidence"]["claims"][0]["sources"][0]["locator"] = "/different"
    reasons = {f["reason"] for f in verify_articles([value], source())["findings"]}
    assert reasons == {"citation_without_verified_quote", "locator_mismatch"}


def test_retrieval_sections_keep_scope_negation_and_exclude_navigation():
    value = article()
    value.update(
        title="Камера",
        body=(
            "# Камера\n\nОбласть источника. [S1]\n\n"
            "## Результат проверки\n\nПосле замены изображение не появилось. [S1]\n\n"
            "## Связанные материалы\n\n[[wheel|Мотор-колесо]]\n"
        ),
    )
    rows = list(retrieval_units(value, {"wheel": "Мотор-колесо"}))
    assert len(rows) == 1
    assert all(value["evidence"]["scope"] in row["content"] for row in rows)
    assert "не появилось" in rows[0]["content"]
    assert all("Мотор-колесо" not in row["content"] for row in rows)
    assert all("[S1]" not in row["content"] and row["kind"] == "note" for row in rows)


def test_reader_metadata_cannot_bypass_private_identifier_filter():
    value = article()
    value["evidence"]["questions"] = ["Что с SDCFLEETOPS-12345?"]
    assert any(
        f["reason"] == "reader_metadata_private"
        for f in verify_articles([value], source())["findings"]
    )


def test_retrieval_never_separates_action_from_prerequisite_or_negative_result():
    value = article()
    value.update(
        title="Камера",
        body="# Камера\n\n## Перед работой\n\nОтключить обе АКБ.\n\n## Замена\n\nОтсоединить модуль.\n\n## Результат\n\nЗамена не помогла.",
    )
    rows = list(retrieval_units(value, {}))
    assert len(rows) == 1
    assert all(
        part in rows[0]["content"]
        for part in ["Отключить обе АКБ", "Отсоединить модуль", "Замена не помогла"]
    )


def grounded_fixture(tmp_path):
    drafts = tmp_path / "drafts"
    article_dir = drafts / "repair"
    article_dir.mkdir(parents=True)
    (article_dir / "camera.md").write_text(
        "<!-- id: camera -->\n# Камера\n\nПосле замены изображение не появилось. [S1]\n"
    )
    evidence = article()
    evidence["evidence"].update(
        article_id="camera",
        title="Камера",
        source_refs=["source:v2:manual#pages-0001-0001"],
        citations={"S1": ["source:v2:manual#pages-0001-0001"]},
    )
    evidence["evidence"]["claims"][0].update(
        basis="manual",
        sources=[
            {
                "source_ref": "source:v2:manual#pages-0001-0001",
                "locator": "pages-0001-0001",
                "excerpt": "После замены изображение не появилось.",
            }
        ],
    )
    (article_dir / "evidence.json").write_text(
        json.dumps({"articles": [evidence["evidence"]]}, ensure_ascii=False)
    )

    originals = tmp_path / "originals"
    originals.mkdir()
    original = originals / "manual.pdf"
    original.write_bytes(b"trusted original")
    row = {
        "source_ref": "source:v2:manual#pages-0001-0001",
        "kind": "document",
        "locator": "pages-0001-0001",
        "text": "После замены изображение не появилось.",
        "relative_path": "manual.pdf",
        "text_sha256": hashlib.sha256(
            "После замены изображение не появилось.".encode()
        ).hexdigest(),
        "file_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
    }
    sources = tmp_path / "sources"
    sources.mkdir()
    ledger = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    (sources / "sources.jsonl").write_text(ledger)
    (sources / "manifest.private.json").write_text(
        json.dumps(
            {
                "records": 1,
                "errors": [],
                "sourcebook_sha256": hashlib.sha256(ledger.encode()).hexdigest(),
            }
        )
    )
    return drafts, sources, originals, original


def test_publish_rejects_original_replaced_after_verification_and_retry_succeeds(
    tmp_path, monkeypatch
):
    drafts, sources, originals, original = grounded_fixture(tmp_path)
    output = tmp_path / "published"
    trusted = original.read_bytes()
    verify = grounded_builder.verify_original_files

    def replace_after_verify(*args, **kwargs):
        result = verify(*args, **kwargs)
        original.write_bytes(b"replaced after verification")
        return result

    monkeypatch.setattr(
        grounded_builder, "verify_original_files", replace_after_verify
    )
    with pytest.raises(ValueError, match="grounding_original_changed"):
        grounded_builder.publish(drafts, sources, originals, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".published.tmp-*"))

    monkeypatch.setattr(grounded_builder, "verify_original_files", verify)
    original.write_bytes(trusted)
    grounded_builder.publish(drafts, sources, originals, output)
    assert (output / "originals/manual.pdf").read_bytes() == trusted


def test_publish_never_overwrites_existing_output(tmp_path):
    drafts, sources, originals, _ = grounded_fixture(tmp_path)
    output = tmp_path / "published"
    output.mkdir()
    marker = output / "keep"
    marker.write_text("existing")
    with pytest.raises(FileExistsError):
        grounded_builder.publish(drafts, sources, originals, output)
    assert marker.read_text() == "existing"
