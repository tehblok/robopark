#!/usr/bin/env python3
"""Validate original evidence and publish a private, connected knowledge reader."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_authored_knowledge import build as build_authored
from scripts.build_authored_knowledge import load_articles
from scripts.grounded_knowledge.render import retrieval_units, write_reader
from scripts.grounded_knowledge.sources import atomic_directory, copy_verified_original
from scripts.grounded_knowledge.verify import (
    load_sources,
    verify_articles,
    verify_original_files,
)


def publish(drafts, sourcebook, originals, output):
    drafts, sourcebook = Path(drafts), Path(sourcebook)
    originals, output = Path(originals), Path(output)
    articles = load_articles(drafts)
    sources = load_sources(sourcebook)
    validation = verify_articles(articles, sources)
    if not validation["valid"]:
        raise ValueError(json.dumps(validation, ensure_ascii=False))
    validation["original_files_checked"] = verify_original_files(
        sources, articles, originals
    )
    # Validate section identities before creating any publishable output.
    titles = {article["id"]: article["title"] for article in articles}
    for article in articles:
        list(retrieval_units(article, titles))
    with atomic_directory(output) as staging:
        build_authored(drafts, staging)
        report = write_reader(staging, articles, validation)
        used = {
            ref for article in articles for ref in article["evidence"]["source_refs"]
        }
        # Only cited source records accompany the reader; the complete ledger is separate.
        with (staging / "sources.private.jsonl").open(
            "x", encoding="utf-8"
        ) as handle:
            for ref in sorted(used):
                handle.write(
                    json.dumps(
                        sources[ref], ensure_ascii=False, separators=(",", ":")
                    )
                    + "\n"
                )
        # Bundle cited originals beside private evidence, never in model context.
        copied = set()
        for article in articles:
            links = []
            for ref in article["evidence"]["source_refs"]:
                row = sources[ref]
                if row["kind"] not in {"document", "reference"}:
                    continue
                relative = Path(row["relative_path"])
                if relative not in copied:
                    target = staging / "originals" / relative
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    copy_verified_original(
                        originals, relative, target, row["file_sha256"]
                    )
                    copied.add(relative)
                link = f"- [{relative.name}](<../originals/{relative.as_posix()}>)"
                if link not in links:
                    links.append(link)
            if links:
                page = staging / "evidence" / (article["id"] + ".md")
                with page.open("a", encoding="utf-8") as handle:
                    handle.write(
                        "\n## Полные оригиналы\n\n" + "\n".join(links) + "\n"
                    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("drafts", "sources", "originals", "output"):
        parser.add_argument("--" + argument, type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        report = publish(args.drafts, args.sources, args.originals, args.output)
    except ValueError as error:
        print(str(error))
        raise SystemExit(1) from error
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
