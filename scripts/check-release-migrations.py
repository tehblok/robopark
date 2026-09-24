"""Reject a release when its declared migration head differs from Alembic."""

import argparse
import json
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError


def check_release_migrations(root: Path) -> list[str]:
    api_root = root / "apps/api"
    try:
        config = Config(str(api_root / "alembic.ini"))
        config.set_main_option("script_location", str(api_root / "alembic"))
        actual = ScriptDirectory.from_config(config).get_current_head()
        declared = json.loads((root / "deploy/release-metadata.json").read_text())[
            "migration_head"
        ]
        policy = json.loads((root / "deploy/migration-policy.json").read_text())[
            "target_head"
        ]
    except (CommandError, KeyError, OSError, TypeError, ValueError) as error:
        return [f"release_migration_check_failed: {error}"]
    if actual == declared == policy == "0041_system_metrics_presence":
        return []
    return [f"actual={actual} metadata={declared} policy={policy}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    args = parser.parse_args()
    findings = check_release_migrations(args.root)
    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        return 1
    print("Release migration heads agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
