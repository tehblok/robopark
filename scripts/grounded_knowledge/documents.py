"""Re-extract document text from original bytes, never trust an old prepared seed."""

from __future__ import annotations

import tempfile
from pathlib import Path

from scripts.build_knowledge_seed import (
    DEFAULT_CHUNK_CHARS,
    DEFAULT_PART_BYTES,
    Builder,
)


def extract_original(source, relative):
    """Use the bounded format readers; macros/scripts are never executed.

    Text extraction is reproducible, but does not establish visual completeness
    or technical correctness of the original document.
    """
    source, relative = Path(source), Path(relative)
    with tempfile.TemporaryDirectory(prefix="robopark-source-") as temporary:
        builder = Builder(
            source, Path(temporary), DEFAULT_CHUNK_CHARS, DEFAULT_PART_BYTES
        )
        rows = []

        class Collector:
            def write(self, row):
                rows.append(row)

        builder.writer = Collector()
        parser = getattr(builder, "read_" + relative.suffix.lower().lstrip("."))
        parser(source / relative, relative)
        if builder.errors:
            raise ValueError("source_extraction_failed")
        return rows
