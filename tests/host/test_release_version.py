import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from robopark_version import BuildIdentity, ReleaseVersion


def test_repository_identity_agrees_across_shipped_sources() -> None:
    checker = importlib.util.spec_from_file_location(
        "check_release_version", SCRIPTS / "check-release-version.py"
    )
    assert checker is not None and checker.loader is not None
    module = importlib.util.module_from_spec(checker)
    checker.loader.exec_module(module)

    expected = (ROOT / "VERSION").read_text().strip()
    version = module.check(ROOT, tag="v" + expected)
    assert version == expected
    assert ReleaseVersion.parse(version).raw == expected


def test_release_documentation_targets_canonical_version() -> None:
    generator = importlib.util.spec_from_file_location(
        "release_docs", SCRIPTS / "generate-release-notes.py"
    )
    assert generator is not None and generator.loader is not None
    module = importlib.util.module_from_spec(generator)
    generator.loader.exec_module(module)

    assert module.expected_compatibility(ROOT)["target_version"] == (
        ROOT / "VERSION"
    ).read_text().strip()


def _write_version_tree(root: Path, version: str = "0.1.45") -> None:
    files = {
        "VERSION": f"{version}\n",
        "apps/api/pyproject.toml": f'[project]\nname = "robopark-api"\nversion = "{version}"\n',
        "apps/api/uv.lock": (
            'version = 1\n\n[[package]]\nname = "robopark-api"\n'
            f'version = "{version}"\nsource = {{ editable = "." }}\n'
        ),
        "apps/web/package.json": json.dumps({"name": "web", "version": version}),
        "apps/web/package-lock.json": json.dumps(
            {"name": "web", "version": version, "packages": {"": {"version": version}}}
        ),
        "apps/api/src/robopark_api/services/ops/context.py": (
            f'APP_VERSION = "{version}"\nOTHER = "unchanged"\n'
        ),
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _run_set_version(root: Path, version: str, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, SCRIPTS / "set-release-version.py", version, mode, "--root", root],
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_version_distinguishes_rc_from_stable() -> None:
    assert ReleaseVersion.parse("0.2.0-rc.1").stage == "rc"
    assert ReleaseVersion.parse("1.0.0").stage == "stable"
    with pytest.raises(ValueError, match="invalid_version"):
        ReleaseVersion.parse("01.0.0")


def test_build_identity_is_deterministic_and_source_bound() -> None:
    first = BuildIdentity.create("1.0.0", "a" * 40, "0036_audit_remediation_state")
    second = BuildIdentity.create("1.0.0", "a" * 40, "0036_audit_remediation_state")
    changed = BuildIdentity.create("1.0.0", "b" * 40, "0036_audit_remediation_state")
    assert first == second
    assert len(first.build_id) == 20
    assert first.build_id != changed.build_id


def test_set_version_write_updates_every_shipped_source(tmp_path: Path) -> None:
    _write_version_tree(tmp_path)
    result = _run_set_version(tmp_path, "0.2.0-rc.1", "--write")
    assert result.returncode == 0, result.stderr

    checker = importlib.util.spec_from_file_location(
        "check_release_version", SCRIPTS / "check-release-version.py"
    )
    assert checker is not None and checker.loader is not None
    module = importlib.util.module_from_spec(checker)
    checker.loader.exec_module(module)
    assert module.check(tmp_path) == "0.2.0-rc.1"
    assert 'OTHER = "unchanged"' in (
        tmp_path / "apps/api/src/robopark_api/services/ops/context.py"
    ).read_text()


def test_set_version_check_is_read_only_and_reports_mismatch(tmp_path: Path) -> None:
    _write_version_tree(tmp_path)
    package = tmp_path / "apps/web/package.json"
    package.write_text(json.dumps({"name": "web", "version": "9.9.9"}))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    result = _run_set_version(tmp_path, "0.1.45", "--check")

    assert result.returncode != 0
    assert "inconsistent_version_sources" in result.stderr
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


@pytest.mark.parametrize(
    ("version", "locked"),
    [("0.2.0-rc.8", "0.2.0rc8"), ("0.2.0-rc.16.dev123", "0.2.0rc16.dev123")],
)
def test_version_check_accepts_uv_normalization_but_not_a_different_release(tmp_path, version, locked):
    _write_version_tree(tmp_path, version)
    lock = tmp_path / "apps/api/uv.lock"
    lock.write_text(lock.read_text().replace(version, locked))
    result = _run_set_version(tmp_path, version, "--check")
    assert result.returncode == 0, result.stderr
    lock.write_text(lock.read_text().replace(locked, "0.2.0rc99"))
    assert _run_set_version(tmp_path, version, "--check").returncode != 0
