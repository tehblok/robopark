#!/usr/bin/env python3
"""Build a private verification ledger from originals; never a RAG ticket dump."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grounded_knowledge.sources import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(
        json.dumps(build(args.source, args.prepared, args.output), ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
