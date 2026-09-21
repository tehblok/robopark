#!/usr/bin/env python3
"""Generate/check source-bound compatibility documentation."""

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = "0.2.0-rc.1"


def expected_compatibility(root=ROOT):
    migration = json.loads((root / "deploy/migration-policy.json").read_text())
    return {
        "schema": 1,
        "target_version": TARGET,
        "migration_head": migration["target_head"],
        "known_heads": migration["known_heads"],
        "bridge_before": migration["bridge_before"],
        "bridge_version": migration["bridge_version"],
        "recovery": migration["recovery"],
        "eligible_channels": ["rc"],
    }


def validate_note(root=ROOT):
    note = (root / f"docs/releases/{TARGET}.md").read_text()
    match = re.search(r"<!-- release-contract:start -->\s*```json\s*(\{.*?\})\s*```\s*<!-- release-contract:end -->", note, re.DOTALL)
    if not match:
        raise ValueError("release_contract_missing")
    contract = json.loads(match.group(1))
    metadata = json.loads((root / "deploy/release-metadata.json").read_text())
    if contract != {"migration_head": metadata["migration_head"], "support_class": "candidate", "support_months": 0, "version": TARGET}:
        raise ValueError("release_contract_mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = expected_compatibility()
    path = ROOT / "docs/releases/compatibility.json"
    validate_note()
    if args.check:
        if json.loads(path.read_text()) != expected:
            raise SystemExit("Release compatibility documentation is stale.")
    else:
        path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n")
    print("Release documentation is current.")


if __name__ == "__main__":
    main()
