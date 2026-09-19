"""Database + config snapshot trees used inside snapshot ZIPs."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from robopark_api.models import Base, User
from robopark_api.security import hash_password
from robopark_api.services.ops.snapshot import (
    SNAPSHOT_DUMP_REL,
    SnapshotError,
    build_snapshot_tree,
    restore_snapshot_tree,
    sqlite_path_from_url,
)
from robopark_api.services.rbac_seed import ensure_rbac_catalog


def _seed_db(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ensure_rbac_catalog(session)
        from robopark_api.services import rbac

        role = rbac.get_role_by_slug(session, rbac.RoleSlug.ROYAL)
        session.add(
            User(
                username="royal",
                password_hash=hash_password("secret"),
                role_id=role.id,
                access_status="approved",
                is_active=True,
            )
        )
        session.commit()
    engine.dispose()


def test_sqlite_path_from_url_absolute(tmp_path: Path):
    db = tmp_path / "x.db"
    url = f"sqlite:///{db}"
    assert sqlite_path_from_url(url) == db.resolve()


def test_build_and_restore_snapshot_tree(tmp_path: Path):
    live = tmp_path / "live"
    live.mkdir()
    db = live / "robopark.db"
    _seed_db(db)
    env = tmp_path / "host.env"
    env.write_text("SECRET_KEY=abc\nCORS_ORIGINS=http://x\n", encoding="utf-8")
    extra = live / "notes.txt"
    extra.write_text("keep-me", encoding="utf-8")
    stale = live / "stale.txt"
    stale.write_text("gone", encoding="utf-8")

    tree = tmp_path / "tree"
    build_snapshot_tree(
        tree,
        database_url=f"sqlite:///{db}",
        config_files={"host.env": env},
        data_dir=live,
    )
    assert (tree / "data" / "robopark.db").is_file()
    assert (tree / "config" / "host.env").read_text(encoding="utf-8").startswith("SECRET_KEY")
    assert (tree / "data" / "notes.txt").read_text(encoding="utf-8") == "keep-me"

    restored_db = tmp_path / "new" / "robopark.db"
    restored_env = tmp_path / "new-host.env"
    new_data = tmp_path / "new-data"
    new_data.mkdir()
    leftover = new_data / "should-go.txt"
    leftover.write_text("wipe-me", encoding="utf-8")
    restore_snapshot_tree(
        tree,
        database_path=restored_db,
        config_targets={"host.env": restored_env},
        data_dir=new_data,
    )
    assert restored_env.read_text(encoding="utf-8").startswith("SECRET_KEY")
    engine = create_engine(f"sqlite:///{restored_db}", future=True)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM users")).scalar()
    engine.dispose()
    assert count == 1
    assert (new_data / "notes.txt").read_text(encoding="utf-8") == "keep-me"
    assert not leftover.exists()


def test_snapshot_skips_symlinks(tmp_path: Path):
    live = tmp_path / "live"
    live.mkdir()
    db = live / "robopark.db"
    _seed_db(db)
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    link = live / "leak.txt"
    link.symlink_to(secret)
    tree = tmp_path / "tree"
    build_snapshot_tree(
        tree,
        database_url=f"sqlite:///{db}",
        config_files={},
        data_dir=live,
    )
    assert not (tree / "data" / "leak.txt").exists()


def test_postgres_snapshot_uses_custom_dump_and_validates_catalog(tmp_path: Path):
    tree = tmp_path / "tree"
    calls: list[list[str]] = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[:2] == ["pg_dump", "--format=custom"]:
            Path(
                next(value.removeprefix("--file=") for value in argv if value.startswith("--file="))
            ).write_bytes(b"PGDMP")
            return ""
        if argv[:2] == ["pg_restore", "--list"]:
            return "TABLE DATA public alembic_version\n"
        if argv[0] == "psql":
            return "0031_postgresql_runtime\n"
        raise AssertionError(argv)

    build_snapshot_tree(
        tree,
        database_url="postgresql+psycopg://robopark@db:5432/robopark",
        expected_head="0031_postgresql_runtime",
        config_files={},
        run=run,
    )

    assert (tree / SNAPSHOT_DUMP_REL).read_bytes() == b"PGDMP"
    assert calls[0][:2] == ["pg_dump", "--format=custom"]
    assert calls[1] == ["pg_restore", "--list", str(tree / SNAPSHOT_DUMP_REL)]
    assert calls[2][0] == "psql"


def test_postgres_snapshot_rejects_foreign_database_head(tmp_path: Path):
    tree = tmp_path / "tree"

    def run(argv, **_kwargs):
        if argv[:2] == ["pg_dump", "--format=custom"]:
            Path(
                next(value.removeprefix("--file=") for value in argv if value.startswith("--file="))
            ).write_bytes(b"PGDMP")
            return ""
        if argv[:2] == ["pg_restore", "--list"]:
            return "TABLE DATA public alembic_version\n"
        if argv[0] == "psql":
            return "foreign-head\n"
        raise AssertionError(argv)

    with pytest.raises(SnapshotError, match="database_dump_invalid"):
        build_snapshot_tree(
            tree,
            database_url="postgresql+psycopg://robopark@db:5432/robopark",
            expected_head="0031_postgresql_runtime",
            config_files={},
            run=run,
        )


def test_postgres_restore_validates_catalog_before_replacing_database(tmp_path: Path):
    tree = tmp_path / "tree"
    dump = tree / SNAPSHOT_DUMP_REL
    dump.parent.mkdir(parents=True)
    dump.write_bytes(b"PGDMP")
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return "TABLE DATA public alembic_version\n" if "--list" in argv else ""

    restore_snapshot_tree(
        tree,
        database_url="postgresql+psycopg://robopark@db:5432/robopark",
        config_targets={},
        run=run,
    )

    assert calls[0] == ["pg_restore", "--list", str(dump)]
    assert calls[1][-2:] == ["--dbname=postgresql://robopark@db:5432/robopark", str(dump)]
