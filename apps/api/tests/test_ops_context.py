"""Ops startup paths for a fresh PostgreSQL installation."""

from robopark_api.config import Settings
from robopark_api.services.ops import context


def test_fresh_postgres_without_optional_attachments_builds_ops_context(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "_migration_head", lambda: "0054_park_coordinates")
    monkeypatch.setattr(context, "_config_files", lambda settings: {})
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://postgres@127.0.0.1:5432/postgres",
        report_attachments_dir=None,
        ops_dir=str(tmp_path / "ops"),
    )

    built = context.build_ops_context(settings)

    assert built.database_url == settings.database_url
    assert built.ops_dir == tmp_path / "ops"
    assert built.data_dir is None


def test_postgres_with_attachments_keeps_configured_data_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(context, "_migration_head", lambda: "0054_park_coordinates")
    monkeypatch.setattr(context, "_config_files", lambda settings: {})
    attachments = tmp_path / "data" / "attachments"
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://postgres@127.0.0.1:5432/postgres",
        report_attachments_dir=str(attachments),
        ops_dir=str(tmp_path / "ops"),
    )

    assert context.build_ops_context(settings).data_dir == attachments.parent
