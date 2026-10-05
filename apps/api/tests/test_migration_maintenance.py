"""Host migration must remain possible while application writes stay forbidden."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from robopark_api.config import reset_settings_cache
from robopark_api.db import configure_engine
from robopark_api.services.ops.maintenance import HostMaintenanceActive


def test_alembic_upgrade_under_maintenance_does_not_unlock_application(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "0055_media_upload_park")
    host = tmp_path / "host"
    for name in ("public", "inbox", "artifacts"):
        (host / name).mkdir(parents=True)
    (host / "public/maintenance.json").write_text('{"enabled":true}')
    monkeypatch.setenv("OPS_HOST_ROOT", str(host))
    reset_settings_cache()
    command.upgrade(cfg, "head")
    engine = configure_engine(url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0059_ai_action_receipts"
            )
        with pytest.raises(HostMaintenanceActive), engine.begin() as connection:
            connection.execute(text("CREATE TABLE forbidden (id INTEGER)"))
    finally:
        engine.dispose()
