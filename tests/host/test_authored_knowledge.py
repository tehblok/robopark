import json

import pytest

from scripts.build_authored_knowledge import build


def draft(
    tmp_path,
    *,
    link="",
    citation="S1",
    claim_sources=True,
    text="Сначала сопоставьте симптом с узлом.",
):
    folder = tmp_path / "drafts/domain"
    folder.mkdir(parents=True)
    (folder / "first.md").write_text(
        f"<!-- id: first -->\n# Диагностика\n\n{text} [{citation}]\n{link}\n"
    )
    record = {
        "article_id": "first",
        "title": "Диагностика",
        "source_refs": ["private-source"],
        "citations": {"S1": ["private-source"]},
        "claims": [
            {
                "claim": "Проверка",
                "basis": "observed",
                "sources": [
                    {
                        "source_ref": "private-source",
                        "excerpt": "Grounding",
                        "locator": "comment",
                    }
                ]
                if claim_sources
                else [],
            }
        ],
    }
    (folder / "evidence.json").write_text(json.dumps({"articles": [record]}))
    return folder.parent


def test_authored_content_reaches_rag_without_private_evidence(tmp_path):
    source = draft(tmp_path)
    result = build(source, tmp_path / "result")
    assert result["articles"] == 1 and not result["expert_reviewed"]
    row = json.loads((tmp_path / "result/seed.jsonl").read_text())
    assert "Сначала сопоставьте симптом" in row["content"]
    assert row["kind"] == "note"
    assert "[S1]" not in row["content"] and "private-source" not in row["content"]
    assert row["source_ref"] == "knowledge:authored-v1:first"
    assert (tmp_path / "result/evidence.private.json").stat().st_mode & 0o777 == 0o600
    assert (
        "[S1](../evidence/first.md#s1)"
        in (tmp_path / "result/articles/first.md").read_text()
    )
    assert "Grounding" in (tmp_path / "result/evidence/first.md").read_text()


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"link": "[[missing]]"}, "authored_link_missing"),
        ({"link": "[[missing|Подробная проверка]]"}, "authored_link_missing"),
        ({"link": "[[broken link]]"}, "authored_link_invalid"),
        ({"citation": "S2"}, "authored_citation_missing"),
        ({"claim_sources": False}, "authored_claim_ungrounded"),
        ({"text": "SDCFLEETOPS-12345"}, "authored_article_private_or_duplicate"),
    ],
)
def test_incomplete_or_private_articles_do_not_produce_partial_package(
    tmp_path, kwargs, reason
):
    source = draft(tmp_path, **kwargs)
    with pytest.raises(ValueError, match=reason):
        build(source, tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_authoring_never_overwrites_source_or_previous_result(tmp_path):
    source = draft(tmp_path)
    with pytest.raises(ValueError, match="overlaps"):
        build(source, source / "result")
    build(source, tmp_path / "result")
    with pytest.raises(FileExistsError):
        build(source, tmp_path / "result")


def test_article_alias_links_survive_in_reader_rag_and_graph(tmp_path):
    source = draft(tmp_path, link="[[second|Условия проверки]]")
    folder = source / "domain"
    (folder / "second.md").write_text(
        "<!-- id: second -->\n# Проверка результата\n\nОбласть применения [S1].\n"
    )
    metadata = json.loads((folder / "evidence.json").read_text())
    second = dict(
        metadata["articles"][0], article_id="second", title="Проверка результата"
    )
    metadata["articles"].append(second)
    (folder / "evidence.json").write_text(json.dumps(metadata))
    build(source, tmp_path / "result")
    assert (
        "[Условия проверки](second.md)"
        in (tmp_path / "result/articles/first.md").read_text()
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / "result/seed.jsonl").read_text().splitlines()
    ]
    assert "Условия проверки" in rows[0]["content"]
    assert "[[" not in rows[0]["content"]
    graph = json.loads((tmp_path / "result/knowledge-map.json").read_text())
    assert graph["edges"] == [{"from": "first", "to": "second", "relation": "see_also"}]


@pytest.mark.parametrize(
    "heading", ["\n## Связанные материалы\n\n", "\nСвязанные статьи: "]
)
def test_navigation_does_not_make_unrelated_article_a_retrieval_match(
    tmp_path, heading
):
    source = draft(tmp_path)
    folder = source / "domain"
    path = folder / "first.md"
    path.write_text(path.read_text() + heading + "[[second|Совсем другой узел]]\n")
    (folder / "second.md").write_text(
        "<!-- id: second -->\n# Другой узел\n\nОписание [S1].\n"
    )
    metadata = json.loads((folder / "evidence.json").read_text())
    metadata["articles"].append(
        dict(metadata["articles"][0], article_id="second", title="Другой узел")
    )
    (folder / "evidence.json").write_text(json.dumps(metadata))
    build(source, tmp_path / "result")
    row = json.loads((tmp_path / "result/seed.jsonl").read_text().splitlines()[0])
    assert "другой узел" not in row["content"]
    assert "Сначала сопоставьте симптом" in row["content"]
    assert "Совсем другой узел" in (tmp_path / "result/articles/first.md").read_text()
