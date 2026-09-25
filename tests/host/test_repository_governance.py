import importlib.util
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_debt_entries_have_owner_target_and_acceptance():
    entries = load("check-tech-debt.py").load_registry()
    assert all(item["owner"] and item["target_version"] and item["acceptance"] for item in entries)


def test_runtime_trust_boundaries_have_no_import_violations():
    assert load("check-module-boundaries.py").boundary_violations(ROOT) == []


def test_retired_release_delivery_is_absent_from_production_code():
    forbidden = re.compile(
        r"ROBOPARK_SIGNING_KEY|release-public-key|github-update|release_signing|"
        r"install-trust|Ed25519|\\.sig\\b"
    )
    roots = [ROOT / name for name in ("apps", "deploy", "scripts", ".github")]
    violations = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix == ".md":
                continue
            if any(
                part in {
                    "node_modules",
                    ".pytest_cache",
                    "__pycache__",
                    ".venv",
                    "dist",
                    "coverage",
                    "tests",
                }
                for part in path.parts
            ):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if forbidden.search(text):
                violations.append(str(path.relative_to(ROOT)))
    assert violations == []
