#!/usr/bin/env python3
"""Build a private, curated repair corpus and an auditable evidence layer.

No model calls, network access or training. Raw chats stay in the review pool;
only manuals, explicit reference tables and supported repair cards enter RAG.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_knowledge_seed import Builder, source_id


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def ready_reference(record, source_map):
    """Source format alone does not establish a maintenance instruction."""
    source = source_map.get(record["source_ref"].split("#", 1)[0], {})
    suffix = Path(source.get("relative_path", "")).suffix.lower()
    if suffix in {".pdf", ".docx"}:
        return record["kind"] == "manual" or (
            record["kind"] == "note"
            and record["title"].startswith("Неполная инструкция:")
        )
    if record["kind"] != "note":
        return False
    if suffix == ".json":
        return record["title"].startswith("Справочник кодов:")
    # Historical stock counts, invoice rows and robot-location tables are not
    # timeless repair knowledge. Keep them in the private review pool.
    diagnostic_source = re.search(
        r"(?i)код[ыа]?\s+(?:ошиб|дефект)|error.?codes?|fault.?codes?",
        Path(source.get("relative_path", "")).stem,
    )
    return (
        suffix in {".xlsx", ".xlsm", ".xls", ".csv"}
        and record["title"].startswith("Справочник:")
        and bool(diagnostic_source)
    )


def assemble(evidence, tracker, output, source_map):
    """Produce the four-field input accepted by the encrypted bundle builder."""
    manifest = json.loads((evidence / "manifest.json").read_text())
    counts, held = Counter(), Counter()
    seen = set()
    digest = hashlib.sha256()
    code_sources = defaultdict(list)
    reference_conflicts = 0
    reference_definition_variants = 0
    reference_duplicates = 0
    with (output / "seed.jsonl").open("x", encoding="utf-8") as target:

        def emit(row):
            if set(row) != {"title", "content", "kind", "source_ref"}:
                raise ValueError("knowledge_record_invalid")
            if row["source_ref"] in seen:
                raise ValueError("knowledge_duplicate_source")
            seen.add(row["source_ref"])
            line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            digest.update(line.encode())
            target.write(line)
            counts[row["kind"]] += 1

        for part in manifest["parts"]:
            with (evidence / part["path"]).open(encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line)
                    if ready_reference(row, source_map):
                        if row["title"].startswith("Справочник"):
                            payload = row["content"].split("\n\n", 1)[-1]
                            matches = re.findall(
                                r"\b([A-Z]{2,4})[-– ](\d{1,3})\s*[-—:]\s*([^\n]+)",
                                payload,
                            )
                            codes = {
                                f"{letters}-{int(number):02d}"
                                for letters, number, _ in matches
                            }
                            if len(codes) == 1:
                                code = codes.pop()
                                labels = {
                                    re.sub(r"\s+", " ", label).strip()
                                    for _, _, label in matches
                                }
                                code_sources[code].append((row, labels))
                            else:
                                held["ambiguous_reference"] += 1
                        else:
                            emit(row)
                    else:
                        held[row["kind"]] += 1
        for code, entries in sorted(code_sources.items()):
            # The same taxonomy exported in JSON and several worksheets is one
            # definition, not independent evidence of a repair dependency.
            labels = {}
            definitions = {}
            for _, values in entries:
                for label in sorted(values):
                    labels.setdefault(label.casefold().replace("ё", "е"), label)
            for row, _ in entries:
                payload = row["content"].split("\n\n", 1)[-1]
                for match in re.finditer(
                    r"(?im)^(?:Описание|Расшифровка|Пояснение|Пример(?:ы)?):\s*(.+(?:\n(?![^\n:]{1,50}:)[^\n]+)*)",
                    payload,
                ):
                    definition = re.sub(r"\s+", " ", match.group(1)).strip()
                    definitions.setdefault(
                        definition.casefold().replace("ё", "е"), definition
                    )
            label_text = "; ".join(labels[key] for key in sorted(labels))
            conflict = len(labels) > 1
            reference_conflicts += int(conflict)
            reference_definition_variants += int(len(definitions) > 1)
            reference_duplicates += len(entries) - 1
            emit(
                {
                    "title": f"Справочник кодов: {code} — {label_text}"[:240],
                    "content": f"Код дефекта: {code}\nНазвание: {label_text}"
                    + "".join(
                        f"\nОписание из справочника: {definitions[key]}"
                        for key in sorted(definitions)
                    )
                    + (
                        "\nСохранены разные варианты описания; их соответствие нужно сверить."
                        if len(definitions) > 1
                        else ""
                    )
                    + (
                        "\nВ исходниках разные названия: требуется сверка с действующим справочником Tracker."
                        if conflict
                        else ""
                    ),
                    "kind": "note",
                    "source_ref": f"reference-code:v1:{code.lower()}",
                }
            )
            # Keep wording variants explicit; they may be complementary rather
            # than contradictory and should not silently overwrite one another.
        cards = 0
        with (tracker / "rag-seed.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    emit(json.loads(line))
                    cards += 1
    (output / "seed.jsonl").chmod(0o600)
    write_json(
        output / "reference-source-map.private.json",
        {
            code: [row["source_ref"] for row, _ in entries]
            for code, entries in sorted(code_sources.items())
        },
    )
    return {
        "documents": sum(counts.values()),
        "documents_by_kind": dict(sorted(counts.items())),
        "repair_cards": cards,
        "reference_duplicates_merged": reference_duplicates,
        "reference_label_conflicts": reference_conflicts,
        "reference_definition_variants": reference_definition_variants,
        "review_pool_by_kind": dict(sorted(held.items())),
        "seed_sha256": digest.hexdigest(),
        "publication": "private_only_encrypt_before_delivery",
        "training": "not_trained_no_automatically_approved_examples",
    }


def build(source: Path, tracker: Path, output: Path):
    if source.is_symlink() or tracker.is_symlink() or output.is_symlink():
        raise ValueError("knowledge_symlink")
    source, tracker, output = source.resolve(), tracker.resolve(), output.resolve()
    if (
        not source.is_dir()
        or not tracker.is_dir()
        or not tracker.is_relative_to(source)
    ):
        raise ValueError("knowledge_input_invalid")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("knowledge_output_overlaps_source")
    missing = [
        name for name in ("pypdf", "xlrd") if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise ValueError(
            "knowledge_readers_missing: install scripts/knowledge-seed-requirements.txt"
        )
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    # Import here so tests can exercise corpus policy without loading the parser.
    from repair_knowledge import compile_export

    tracker_report = compile_export(tracker, output / "tracker")
    evidence = output / "evidence"
    evidence.mkdir(mode=0o700)
    builder = Builder(
        source, evidence, 12_000, 64 * 1024**2, excluded_directories=(tracker,)
    )
    manifest = builder.build()
    write_json(evidence / "manifest.json", manifest)
    write_json(evidence / "source-map.private.json", builder.source_map)
    # The separately compiled tracker files never also become generic documents.
    assert source_id(tracker.relative_to(source)) not in builder.source_map
    report = assemble(evidence, output / "tracker", output, builder.source_map)
    report.update(
        schema=1,
        evidence=manifest,
        tracker=tracker_report,
        ready_for_delivery=not manifest["errors"]
        and not tracker_report.get("records_invalid"),
    )
    write_json(output / "report.json", report)
    summary = (
        "# База знаний ремонта\n\n"
        f"В поисковом слое: {report['documents']} документов, "
        f"из них {report['repair_cards']} обобщённых карточек ремонта.\n\n"
        f"На проверке, вне поиска: {dict(report['review_pool_by_kind'])}. "
        "Сырые чаты и навыки другой ИИ не становятся инструкциями.\n\n"
        "Связи из истории — наблюдения, не причинность и не нормы ремонта. "
        "Все автоматически извлечённые материалы сохраняют статус непроверенного опыта. "
        "Числа поддержки не означают вероятность успешного ремонта.\n\n"
        "`tracker/` содержит структурированные случаи, карточки, словарь и отчёт качества. "
        "`evidence/` сохраняет очищенные инструкции, справочники и кандидатов из чатов. "
        "`seed.jsonl` — вход для build_private_knowledge.py с --replace-public. "
        "Каталог приватный; публиковать только после age-шифрования.\n\n"
        "Модель не дообучалась. Сначала нужны проверенные ответы механиков и отдельный "
        "тестовый набор без пересечения роботов/связанных инцидентов с обучением.\n"
    )
    (output / "README.md").write_text(summary)
    for file in output.rglob("*"):
        if file.is_file():
            file.chmod(0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("Данные для базы"))
    parser.add_argument(
        "--tracker", type=Path, default=Path("Данные для базы/tracker_year_all_parks")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    report = build(args.input, args.tracker, args.output)
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("documents", "repair_cards", "review_pool_by_kind")
            },
            ensure_ascii=False,
        )
    )
    if not report["ready_for_delivery"]:
        raise SystemExit("knowledge_build_incomplete: inspect private report.json")


if __name__ == "__main__":
    main()
