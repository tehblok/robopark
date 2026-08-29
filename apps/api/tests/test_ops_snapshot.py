"""SQLite + config snapshot trees used inside snapshot ZIPs."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from robopark_api.models import Base, User
from robopark_api.security import hash_password
from robopark_api.services.ops.snapshot import (
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
