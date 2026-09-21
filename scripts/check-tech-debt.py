#!/usr/bin/env python3
"""Validate the bounded technical-debt registry."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = {"id", "module", "impact", "risk", "owner", "target_version", "acceptance", "status", "evidence"}


def load_registry(path=ROOT / "docs/technical-debt/registry.yaml"):
    entries = json.loads(path.read_text())
    if not isinstance(entries, list) or len(entries) > 500:
        raise ValueError("invalid_technical_debt_registry")
    seen = set()
    for item in entries:
        if (
            not isinstance(item, dict)
            or set(item) != FIELDS
            or not re.fullmatch(r"TD-[0-9]{3,6}", item["id"])
            or item["id"] in seen
            or item["status"] not in {"open", "accepted", "closed"}
            or item["risk"] not in {"security", "data", "stability", "performance", "maintainability", "cosmetic"}
            or not all(isinstance(item[key], str) and item[key].strip() for key in ("module", "impact", "owner", "target_version", "acceptance"))
            or (item["status"] == "closed" and not isinstance(item["evidence"], str))
            or (item["status"] != "closed" and item["evidence"] is not None)
        ):
            raise ValueError("invalid_technical_debt_registry")
        seen.add(item["id"])
    return entries


if __name__ == "__main__":
    load_registry()
    print("Technical-debt registry is valid.")
