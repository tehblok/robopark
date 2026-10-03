"""The clean-install schema declared for packaging is the actual Alembic head."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "release_migration_checker", ROOT / "scripts/check-release-migrations.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def test_current_declarations_match_actual_alembic_head():
    assert checker.check_release_migrations(ROOT) == []


def test_synchronized_unrelated_head_is_rejected(tmp_path):
    api = tmp_path / "apps/api"
    versions = api / "alembic/versions"
    versions.mkdir(parents=True)
    (api / "alembic.ini").write_text("[alembic]\nscript_location = alembic\n")
    (versions / "unrelated.py").write_text(
        "revision = 'unrelated'\ndown_revision = None\n"
    )
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "release-metadata.json").write_text(json.dumps({"migration_head": "unrelated"}))
    (deploy / "migration-policy.json").write_text(json.dumps({"target_head": "unrelated"}))
    assert checker.check_release_migrations(tmp_path) == [
        "actual=unrelated metadata=unrelated policy=unrelated"
    ]
