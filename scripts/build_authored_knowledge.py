#!/usr/bin/env python3
"""Package authored, connected knowledge articles with a private evidence register.

This validates structure and provenance links, not the truth of repair advice.
Content is authored from sources before this step; this script never turns ticket
counts into an explanation or automatically promotes a claim to a repair rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

IDENTITY = re.compile(r"<!-- id: ([a-z][a-z0-9-]{2,70}) -->")
LINK = re.compile(r"\[\[([a-z][a-z0-9-]{2,70})(?:\|([^\[\]\n|]+))?\]\]")
CITATION = re.compile(r"\[(S\d+)\]")
PRIVATE_MARKERS = re.compile(
    r"\b(?:SDCFLEETOPS|SDGWORKS|ROBOMAINT)-\d+\b|\b(?:repair-case:v1|source:v2):|"
    r"\[[aAаА]\d{2,7}\]|https?://|(?<!\w)@[\w.-]+|"
    r"-----BEGIN .*PRIVATE KEY|(?i:password|token|пароль)\s*[:=]\s*\S+"
)
MAX_ARTICLE_BYTES = 90_000


def _read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024**2:
        raise ValueError("authored_source_invalid")
    return path.read_text(encoding="utf-8")


def load_articles(drafts):
    drafts = Path(drafts)
    if drafts.is_symlink() or not drafts.is_dir():
        raise ValueError("authored_root_invalid")
    metadata, content = {}, {}
    for folder in sorted(drafts.iterdir()):
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("authored_folder_invalid")
        value = json.loads(_read(folder / "evidence.json"))
        if not isinstance(value, dict) or not isinstance(value.get("articles"), list):
            raise TypeError("authored_evidence_invalid")
        for record in value["articles"]:
            identifier = record.get("article_id") if isinstance(record, dict) else None
            if (
                not isinstance(identifier, str)
                or not re.fullmatch(r"[a-z][a-z0-9-]{2,70}", identifier)
                or identifier in metadata
            ):
                raise ValueError("authored_id_invalid")
            metadata[identifier] = record
        for path in sorted(folder.glob("*.md")):
            raw = _read(path)
            first, separator, body = raw.partition("\n")
            match = IDENTITY.fullmatch(first.strip())
            if not match or not separator or len(body.encode()) > MAX_ARTICLE_BYTES:
                raise ValueError("authored_article_invalid")
            identifier = match.group(1)
            if (
                identifier in content
                or not body.startswith("# ")
                or PRIVATE_MARKERS.search(body)
            ):
                raise ValueError("authored_article_private_or_duplicate")
            content[identifier] = body.strip()
    if not content or content.keys() != metadata.keys():
        raise ValueError("authored_evidence_missing")
    articles = []
    for identifier, body in sorted(content.items()):
        record = metadata[identifier]
        title = body.splitlines()[0][2:].strip()
        refs = record.get("source_refs")
        aliases = record.get("citations")
        claims = record.get("claims")
        if (
            record.get("title") != title
            or len(title) > 240
            or not isinstance(refs, list)
            or not refs
            or not all(isinstance(ref, str) and ref for ref in refs)
            or not isinstance(aliases, dict)
            or not isinstance(claims, list)
            or not claims
        ):
            raise ValueError("authored_provenance_invalid")
        for alias, sources in aliases.items():
            if (
                not isinstance(alias, str)
                or not re.fullmatch(r"S\d+", alias)
                or not isinstance(sources, list)
                or not sources
                or not all(
                    isinstance(source, str) and source in refs for source in sources
                )
            ):
                raise ValueError("authored_citation_invalid")
        for alias in set(CITATION.findall(body)):
            sources = aliases.get(alias)
            if (
                not isinstance(sources, list)
                or not sources
                or not set(sources) <= set(refs)
            ):
                raise ValueError("authored_citation_missing")
        for claim in claims:
            if (
                not isinstance(claim, dict)
                or claim.get("basis") not in {"manual", "observed", "editorial"}
                or not isinstance(claim.get("claim"), str)
                or not claim["claim"]
            ):
                raise ValueError("authored_claim_invalid")
            sources = claim.get("sources")
            if not isinstance(sources, list) or (
                claim["basis"] != "editorial" and not sources
            ):
                raise ValueError("authored_claim_ungrounded")
            for source in sources:
                if (
                    not isinstance(source, dict)
                    or source.get("source_ref") not in refs
                    or not isinstance(source.get("excerpt"), str)
                    or not source["excerpt"].strip()
                    or not isinstance(source.get("locator"), str)
                ):
                    raise ValueError("authored_claim_source_invalid")
        if "[[" in LINK.sub("", body) or "]]" in LINK.sub("", body):
            raise ValueError("authored_link_invalid")
        related = sorted({match[1] for match in LINK.finditer(body)})
        if not set(related) <= content.keys() or identifier in related:
            raise ValueError("authored_link_missing")
        articles.append(
            {
                "id": identifier,
                "title": title,
                "body": body,
                "related": related,
                "evidence": record,
            }
        )
    return articles


def build(drafts, output):
    drafts, output = Path(drafts), Path(output)
    if drafts.is_symlink() or output.is_symlink():
        raise ValueError("authored_root_invalid")
    root, destination = drafts.resolve(), output.resolve()
    if root.is_relative_to(destination) or destination.is_relative_to(root):
        raise ValueError("authored_output_overlaps_input")
    articles = load_articles(drafts)
    titles = {article["id"]: article["title"] for article in articles}
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    pages = output / "articles"
    pages.mkdir(mode=0o700)
    evidence_pages = output / "evidence"
    evidence_pages.mkdir(mode=0o700)
    graph = {"schema": 1, "nodes": [], "edges": []}
    index = [
        "# База знаний ремонта Robopark",
        "",
        "Статьи объясняют, как разбирать неисправность, выбирать проверку и принимать результат. Тикеты и статистика находятся в слое источников; они не заменяют объяснения.",
        "",
        "## Начать с вопроса",
        "",
    ]
    with (output / "seed.jsonl").open("x", encoding="utf-8") as seed:
        for article in articles:
            identifier, title = article["id"], article["title"]
            readable = LINK.sub(
                lambda m: f"[{m[2] or titles[m[1]]}]({m[1]}.md)",
                article["body"],
            )
            readable = CITATION.sub(
                lambda m, article_id=identifier: (
                    f"[{m[1]}](../evidence/{article_id}.md#{m[1].lower()})"
                ),
                readable,
            )
            (pages / f"{identifier}.md").write_text(readable + "\n", encoding="utf-8")
            _write_evidence_page(evidence_pages, article)
            index.append(f"- [{title}](articles/{identifier}.md)")
            graph["nodes"].append(
                {"id": identifier, "title": title, "kind": "authored_knowledge"}
            )
            graph["edges"].extend(
                {"from": identifier, "to": target, "relation": "see_also"}
                for target in article["related"]
            )
            # Cite the article itself in model responses. Internal [S1] labels
            # resolve only in the private editor register, not to public URLs.
            # Navigation is for readers and the graph, not retrieval evidence.
            # Otherwise every linked component becomes a false topic match.
            body = re.sub(
                r"(?ms)^## Связанные (?:материалы|статьи)\s*\n.*?(?=^## |\Z)",
                "",
                article["body"],
            )
            body = re.sub(r"(?m)^Связанные статьи:.*$", "", body)
            body = CITATION.sub("", body).strip()
            body = LINK.sub(lambda m: m[2] or titles[m[1]], body)
            seed.write(
                json.dumps(
                    {
                        "title": title,
                        "content": "Тип: составленная статья базы знаний; техническая проверка экспертом не заявлена.\n\n"
                        + body,
                        "kind": "note",
                        "source_ref": f"knowledge:authored-v1:{identifier}",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    index.extend(
        [
            "",
            "## Как читать",
            "",
            "Утверждения из инструкций, наблюдения из ремонтов и редакционные предложения различаются в тексте. Неполные источники не дают права достраивать процедуру. Статьи ещё не утверждены техническим экспертом. Ссылки S1, S2 и далее открывают приватные страницы оснований. Они и реестр `evidence.private.json` не передаются всем пользователям помощника. Весь каталог хранится приватно; проверка маркеров в сборщике не заменяет проверку перед публикацией.",
            "",
            "Модель не обучалась. Эти статьи предназначены для поиска и подачи релевантного знания в её контекст.",
        ]
    )
    (output / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    (output / "knowledge-map.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "evidence.private.json").write_text(
        json.dumps(
            {"articles": [article["evidence"] for article in articles]},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    report = {
        "schema": 1,
        "articles": len(articles),
        "connections": len(graph["edges"]),
        "claims": sum(len(article["evidence"]["claims"]) for article in articles),
        "expert_reviewed": False,
        "automatically_generated_from_ticket_counts": False,
        "seed_sha256": hashlib.sha256((output / "seed.jsonl").read_bytes()).hexdigest(),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    for path in output.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    return report


def _write_evidence_page(folder, article):
    record = article["evidence"]
    lines = [
        f"# Основания: {article['title']}",
        "",
        "Приватный редакционный реестр. Сообщение в источнике не означает независимую проверку результата или утверждение процедуры экспертом.",
        "",
        f"[Вернуться к статье](../articles/{article['id']}.md)",
    ]
    for alias, refs in sorted(
        record["citations"].items(), key=lambda item: int(item[0][1:])
    ):
        lines.extend(["", f"## {alias}", ""])
        lines.extend(f"- Источник: `{ref}`" for ref in refs)
        for claim in record["claims"]:
            sources = [s for s in claim["sources"] if s["source_ref"] in refs]
            if not sources:
                continue
            basis = {
                "manual": "Документ или контракт",
                "observed": "Наблюдение в источнике",
                "editorial": "Редакционное предложение",
            }[claim["basis"]]
            lines.extend(["", f"**{basis}:** {claim['claim']}", ""])
            for source in sources:
                lines.append(
                    f"Расположение: `{source['locator']}`; `{source['source_ref']}`."
                )
                lines.extend("> " + line for line in source["excerpt"].splitlines())
                lines.append("")
    (folder / f"{article['id']}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drafts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(json.dumps(build(args.drafts, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
