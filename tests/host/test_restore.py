"""PostgreSQL dump validation and destructive clean-install safety."""

import importlib.util
from pathlib import Path

import pytest

from robopark_host.release import ReleaseError
from robopark_host.restore import validate_postgres_dump


def _configure_module():
    path = Path("deploy/installer/lib/configure.py")
    spec = importlib.util.spec_from_file_location("installer_configure", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("target", [Path("/"), Path("/var"), Path("/var/lib"), Path(".")])
def test_clean_reinstall_refuses_broad_or_unresolved_targets(tmp_path, target):
    configure = _configure_module()

    with pytest.raises(ValueError, match="unsafe_data_root"):
        configure.validate_clean_data_root(target, expected=tmp_path / "var/lib/robopark")


def test_clean_reinstall_refuses_symlink_even_when_it_resolves_to_named_root(tmp_path):
    configure = _configure_module()
    expected = tmp_path / "var/lib/robopark"
    expected.mkdir(parents=True)
    alias = tmp_path / "robopark-link"
    alias.symlink_to(expected)

    with pytest.raises(ValueError, match="unsafe_data_root"):
        configure.validate_clean_data_root(alias, expected=expected)


def test_clean_reinstall_accepts_only_exact_existing_named_root(tmp_path):
    configure = _configure_module()
    expected = tmp_path / "var/lib/robopark"
    expected.mkdir(parents=True)

    assert configure.validate_clean_data_root(expected, expected=expected) == expected.resolve()


def test_clean_reinstall_requires_exact_local_confirmation_before_deletion(tmp_path):
    configure = _configure_module()
    expected = tmp_path / "var/lib/robopark"
    expected.mkdir(parents=True)
    (expected / "robopark.db").write_text("old sqlite must not be imported")

    with pytest.raises(ValueError, match="confirmation_required"):
        configure.clean_reinstall_data_root(
            expected,
            expected=expected,
            confirmation="DELETE /var/lib",
        )
    assert (expected / "robopark.db").exists()

    configure.clean_reinstall_data_root(
        expected,
        expected=expected,
        confirmation="DELETE ROBOPARK DATA",
    )
    assert not expected.exists()


def test_postgres_dump_validation_lists_custom_dump_and_checks_alembic_head(tmp_path):
    dump = tmp_path / "robopark.dump"
    dump.write_bytes(b"PGDMP fixture")
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ["pg_restore", "--list"]:
            return "TABLE DATA public alembic_version\nTABLE DATA public users\n"
        return "0031_postgresql_runtime\n"

    validate_postgres_dump(
        dump,
        expected_head="0031_postgresql_runtime",
        candidate_dsn="postgresql://robopark@127.0.0.1/robopark_restore_candidate",
        run=run,
    )

    assert calls[0] == ["pg_restore", "--list", str(dump)]
    assert "robopark_restore_candidate" in " ".join(calls[1])
    assert all("/robopark " not in " ".join(call) for call in calls[1:])


def test_postgres_dump_validation_rejects_missing_alembic_catalog(tmp_path):
    dump = tmp_path / "robopark.dump"
    dump.write_bytes(b"PGDMP fixture")

    with pytest.raises(ReleaseError, match="snapshot_invalid"):
        validate_postgres_dump(
            dump,
            expected_head="0031_postgresql_runtime",
            candidate_dsn="postgresql://robopark@127.0.0.1/candidate",
            run=lambda _argv, **_kwargs: "TABLE DATA public users\n",
        )
