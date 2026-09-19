"""PostgreSQL dump validation and destructive clean-install safety."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

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


@pytest.mark.parametrize(
    "target", [Path("/"), Path("/var"), Path("/var/lib"), Path(".")]
)
def test_clean_reinstall_refuses_broad_or_unresolved_targets(tmp_path, target):
    configure = _configure_module()

    with pytest.raises(ValueError, match="unsafe_data_root"):
        configure.validate_clean_data_root(target, trusted_base=tmp_path)


def test_clean_reinstall_refuses_symlink_even_when_it_resolves_to_named_root(tmp_path):
    configure = _configure_module()
    expected = tmp_path / "var/lib/robopark"
    expected.mkdir(parents=True)
    alias = tmp_path / "robopark-link"
    alias.symlink_to(expected)

    with pytest.raises(ValueError, match="unsafe_data_root"):
        configure.validate_clean_data_root(alias, trusted_base=tmp_path)


def test_clean_reinstall_refuses_symlink_in_any_target_ancestor(tmp_path):
    configure = _configure_module()
    outside = tmp_path / "outside"
    (outside / "robopark").mkdir(parents=True)
    (tmp_path / "var").mkdir()
    (tmp_path / "var/lib").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="unsafe_data_root"):
        configure.validate_clean_data_root(
            tmp_path / "var/lib/robopark", trusted_base=tmp_path
        )


def test_clean_reinstall_accepts_only_exact_existing_named_root(tmp_path):
    configure = _configure_module()
    expected = tmp_path / "var/lib/robopark"
    expected.mkdir(parents=True)

    assert (
        configure.validate_clean_data_root(expected, trusted_base=tmp_path)
        == expected.resolve()
    )


def test_clean_reinstall_requires_exact_local_confirmation_before_deletion(tmp_path):
    configure = _configure_module()
    expected = tmp_path / "var/lib/robopark"
    expected.mkdir(parents=True)
    (expected / "robopark.db").write_text("old sqlite must not be imported")

    with pytest.raises(ValueError, match="confirmation_required"):
        configure.clean_reinstall_data_root(
            expected,
            trusted_base=tmp_path,
            confirmation="DELETE /var/lib",
        )
    assert (expected / "robopark.db").exists()

    configure.clean_reinstall_data_root(
        expected,
        trusted_base=tmp_path,
        confirmation="DELETE ROBOPARK DATA",
    )
    assert not expected.exists()


def _updater_journal(phase: str) -> dict:
    terminal = phase in {"succeeded", "rolled_back"}
    return {
        "schema": 1,
        "job_id": str(uuid4()),
        "actor_user_id": 1,
        "candidate": "1.0.1-candidate",
        "previous": "1.0.0",
        "previous_config": "compose/previous.json",
        "original_previous": None,
        "phase": phase,
        "migration_started": phase == "succeeded",
        "writes_resumed": terminal,
        "snapshot_done": terminal,
        "cutover_started": terminal,
        "publication_degraded": False,
        "error": None if phase == "succeeded" else "interrupted",
    }


def _restore_journal(phase: str) -> dict:
    terminal = phase in {"succeeded", "rolled_back"}
    identity = str(uuid4())
    return {
        "schema": 2,
        "request": {
            "job_id": identity,
            "kind": "restore",
            "actor_user_id": 1,
            "artifact": f"restore-{identity}.zip",
            "sha256": "a" * 64,
            "created_at": "2026-09-20T00:00:00+00:00",
        },
        "phase": phase,
        "snapshot_done": terminal,
        "writes_resumed": terminal,
        "error": None if phase == "succeeded" else "interrupted",
        "publication_degraded": False,
        "database_profile": "postgresql-17",
    }


@pytest.mark.parametrize(
    ("filename", "journal"),
    [
        ("updater-journal.json", _updater_journal("succeeded")),
        ("updater-journal.json", _updater_journal("rolled_back")),
        ("updater-journal.json", _updater_journal("failed")),
        ("restore-journal.json", _restore_journal("succeeded")),
        ("restore-journal.json", _restore_journal("rolled_back")),
        ("restore-journal.json", _restore_journal("failed")),
    ],
)
def test_clean_reinstall_accepts_consistent_terminal_journals(
    tmp_path, filename, journal
):
    configure = _configure_module()
    state = tmp_path / "ops/state"
    state.mkdir(parents=True)
    (state / filename).write_text(json.dumps(journal))

    assert configure.clean_host_state_is_idle(tmp_path)


@pytest.mark.parametrize(
    ("filename", "journal"),
    [
        ("updater-journal.json", _updater_journal("activating")),
        ("restore-journal.json", _restore_journal("replacing")),
    ],
)
def test_clean_reinstall_rejects_nonterminal_journals(tmp_path, filename, journal):
    configure = _configure_module()
    state = tmp_path / "ops/state"
    state.mkdir(parents=True)
    (state / filename).write_text(json.dumps(journal))

    assert not configure.clean_host_state_is_idle(tmp_path)


@pytest.mark.parametrize("contents", ["not json", '{"phase":"succeeded"}'])
def test_clean_reinstall_fails_closed_for_malformed_journal(tmp_path, contents):
    configure = _configure_module()
    state = tmp_path / "ops/state"
    state.mkdir(parents=True)
    (state / "updater-journal.json").write_text(contents)

    assert not configure.clean_host_state_is_idle(tmp_path)


def test_clean_reinstall_rejects_inconsistent_terminal_journal(tmp_path):
    configure = _configure_module()
    state = tmp_path / "ops/state"
    state.mkdir(parents=True)
    journal = _updater_journal("succeeded")
    journal["writes_resumed"] = False
    (state / "updater-journal.json").write_text(json.dumps(journal))

    assert not configure.clean_host_state_is_idle(tmp_path)


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
    assert "--username=robopark" in calls[1]
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
