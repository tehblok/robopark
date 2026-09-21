#!/usr/bin/env python3
"""Reject imports that cross documented runtime trust boundaries."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _contract(path):
    text = path.read_text()
    match = re.search(r"```yaml\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match is None:
        raise ValueError("module_contract_missing")
    return json.loads(match.group(1))


def boundary_violations(root=ROOT):
    contract = _contract(root / "docs/architecture/module-contracts.md")
    if contract.get("schema") != 1 or not isinstance(contract.get("boundaries"), list):
        raise ValueError("invalid_module_contract")
    violations = []
    for boundary in contract["boundaries"]:
        source = root / boundary["root"]
        forbidden = tuple(boundary["forbidden_imports"])
        for path in sorted(source.rglob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), start=1):
                match = re.match(r"\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.]*)", line)
                name = match.group(1) if match else None
                if name and any(name == item or name.startswith(item + ".") for item in forbidden):
                    violations.append(f"{path.relative_to(root)}:{number}:{name}")
    return violations


if __name__ == "__main__":
    problems = boundary_violations()
    if problems:
        raise SystemExit("Forbidden module imports:\n" + "\n".join(problems))
    print("Module boundaries are valid.")
