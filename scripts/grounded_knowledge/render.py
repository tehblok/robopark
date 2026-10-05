"""Reader navigation and scoped retrieval units for the same connected knowledge."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

ROLES = {
    "mechanic": "Механик",
    "operator": "Оператор",
    "admin": "Администратор / операционная работа",
}
CITATION = re.compile(r"\[S\d+\]")
LINK = re.compile(r"\[\[([a-z][a-z0-9-]+)(?:\|([^\[\]\n|]+))?\]\]")
SOURCE_HEADER = "База знаний Robopark\n"


def retrieval_units(article, titles):
    """Retain scope with every section; navigation never becomes topic evidence."""
    record = article["evidence"]
    body = re.sub(
        r"(?ms)^## Связанные (?:материалы|статьи)\s*\n.*?(?=^## |\Z)",
        "",
        article["body"],
    )
    body = re.sub(r"(?m)^Связанные (?:статьи|материалы):.*$", "", body)
    body = CITATION.sub("", body)
    body = LINK.sub(lambda match: match[2] or titles[match[1]], body)
    # Keep prerequisites, actions, counterexamples and outcomes together. Search
    # indexes internal chunks, but generation receives the complete article.
    yield {
        "source_ref": f"knowledge:grounded-v2:{article['id']}",
        "title": article["title"],
        "content": SOURCE_HEADER
        + f"Область применения: {record['scope']}\n"
        + "Сведения инструкций и архивных сообщений различаются в тексте. Экспертное утверждение статьи не заявлено.\n\n"
        + body,
        "kind": "note",
    }


def write_reader(output, articles, validation):
    titles = {article["id"]: article["title"] for article in articles}
    rows = [row for article in articles for row in retrieval_units(article, titles)]
    with (output / "seed.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    index = [
        "# Знания Robopark: ремонт, инженерный разбор и операционная работа",
        "",
        "Это общая база, построенная по инструкциям, чатам и тикетам. Выберите рабочий вопрос; в статье указаны применимость, конкретный опыт, основания и то, что осталось неизвестным.",
        "",
        "[Связи между темами](connections.md) · [Вопросы к базе](questions.md)",
        "",
    ]
    for role, label in ROLES.items():
        index.extend([f"## {label}", ""])
        for article in articles:
            if role in article["evidence"]["roles"]:
                index.append(f"- [{article['title']}](articles/{article['id']}.md)")
        index.append("")
    index.extend(
        [
            "## Инженерный вход",
            "",
            "Связи узлов, сигналов, конфигурации, проверок и результатов находятся в тех же статьях. Карта тем помогает перейти от одного узла к связанному; наличие связи в карте не доказывает причинность отказа.",
            "",
            "## Основания и границы",
            "",
            "S1, S2 и другие ссылки открывают точные выдержки из источников. Рекомендация в чате не обозначает выполненную работу; запись о проверке не подтверждает исправность другого робота. Временные правила и состояния относятся к дате источника. Пропущенные изображения и противоречия обозначены в статьях.",
            "",
            "Реестр и цитаты приватные; каталог распространяется зашифрованным. Сопоставление цитат проверено автоматически, смысл — отдельным ревью; это не экспертная сертификация. Gemma на этих данных не дообучалась.",
        ]
    )
    (output / "README.md").write_text("\n".join(index) + "\n")
    entities = defaultdict(list)
    questions = [
        "# Рабочие вопросы",
        "",
        "Каждый вопрос ведёт к статье с основаниями ответа.",
        "",
    ]
    graph = {"schema": 2, "nodes": [], "edges": [], "causal_graph": False}
    for article in articles:
        record = article["evidence"]
        graph["nodes"].append(
            {
                "id": article["id"],
                "type": "article",
                "title": article["title"],
                "roles": record["roles"],
            }
        )
        for entity in record["entities"]:
            entities[entity.strip().casefold()].append(article)
        questions.extend(
            f"- [{question}](articles/{article['id']}.md)"
            for question in record["questions"]
        )
        for index, claim in enumerate(record["claims"]):
            identifier = f"{article['id']}:claim:{index + 1}"
            graph["nodes"].append(
                {
                    "id": identifier,
                    "type": "claim",
                    "text": claim["claim"],
                    "basis": claim["basis"],
                }
            )
            graph["edges"].append(
                {"from": article["id"], "to": identifier, "relation": "explains"}
            )
        graph["edges"].extend(
            {"from": article["id"], "to": other, "relation": "see_also"}
            for other in article["related"]
        )
    connections = [
        "# Связи между темами",
        "",
        "Общие узлы и вопросы связывают статьи. Это навигация, а не доказанная причинная цепь.",
        "",
    ]
    for entity, related in sorted(entities.items()):
        identifier = "concept:" + hashlib.sha256(entity.encode()).hexdigest()[:16]
        graph["nodes"].append({"id": identifier, "type": "concept", "title": entity})
        graph["edges"].extend(
            {"from": article["id"], "to": identifier, "relation": "discusses"}
            for article in related
        )
        connections.append(
            f"- **{entity}**: "
            + "; ".join(
                f"[{article['title']}](articles/{article['id']}.md)"
                for article in related
            )
        )
    (output / "questions.md").write_text("\n".join(questions) + "\n")
    (output / "connections.md").write_text("\n".join(connections) + "\n")
    (output / "knowledge-map.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2) + "\n"
    )
    report = dict(
        validation,
        rag_documents=len(rows),
        articles_by_role={
            role: sum(role in a["evidence"]["roles"] for a in articles)
            for role in ROLES
        },
        seed_sha256=hashlib.sha256((output / "seed.jsonl").read_bytes()).hexdigest(),
    )
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    for path in output.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    return report
