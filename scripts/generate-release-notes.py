#!/usr/bin/env python3
"""Generate/check source-bound compatibility documentation."""

import argparse
import json
import re
from pathlib import Path
from release_policy import SupportPolicy

ROOT = Path(__file__).resolve().parent.parent


def target_version(root=ROOT):
    return (root / "VERSION").read_text().strip()


def expected_compatibility(root=ROOT):
    migration = json.loads((root / "deploy/migration-policy.json").read_text())
    return {
        "schema": 1,
        "target_version": target_version(root),
        "migration_head": migration["target_head"],
        "known_heads": migration["known_heads"],
        "bridge_before": migration["bridge_before"],
        "bridge_version": migration["bridge_version"],
        "recovery": migration["recovery"],
        "eligible_channels": list(SupportPolicy.from_file(root / "deploy/support-policy.json").release(target_version(root)).eligible_channels),
    }


def validate_note(root=ROOT):
    version = target_version(root)
    note = (root / f"docs/releases/{version}.md").read_text()
    match = re.search(r"<!-- release-contract:start -->\s*```json\s*(\{.*?\})\s*```\s*<!-- release-contract:end -->", note, re.DOTALL)
    if not match:
        raise ValueError("release_contract_missing")
    contract = json.loads(match.group(1))
    metadata = json.loads((root / "deploy/release-metadata.json").read_text())
    support = SupportPolicy.from_file(root / "deploy/support-policy.json").release(version)
    if contract != {"migration_head": metadata["migration_head"], "support_class": support.support_class, "support_months": support.support_months, "version": version}:
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
