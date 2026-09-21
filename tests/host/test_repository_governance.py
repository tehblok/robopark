import importlib.util
from pathlib import Path

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
