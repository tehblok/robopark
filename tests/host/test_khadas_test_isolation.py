from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from scripts import khadas_test_isolation as isolation


class DatabaseRunner:
    def __init__(self, *, fail: str = ""):
        self.calls = []
        self.fail = fail

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if self.fail and self.fail in command:
            raise subprocess.CalledProcessError(
                1, command, stderr=b"private-database-details"
            )
        if "pg_dump" in command:
            kwargs["stdout"].write(b"PGDMP" + b"test-backup" * 32)
        if "psql" in command:
            assert b"BEGIN;" in kwargs["input"]
        return subprocess.CompletedProcess(command, 0)


def root_layout(tmp_path: Path):
    config = tmp_path / "var/lib/robopark/ops/state/current-compose.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"services":{"db":{}}}')
    backup = tmp_path / "var/lib/robopark-test-transition"
    backup.mkdir(mode=0o700)
    return backup


def test_backup_is_verified_before_mutation_and_never_replaced(tmp_path):
    backup = root_layout(tmp_path)
    runner = DatabaseRunner()
    isolation.isolate(tmp_path, runner, backup)
    dump = backup / "before-test-isolation.dump"
    original = dump.read_bytes()
    assert dump.stat().st_mode & 0o777 == 0o600
    commands = [
        next(item for item in cmd if item in {"pg_dump", "pg_restore", "psql"})
        for cmd in runner.calls
    ]
    assert commands == ["pg_dump", "pg_restore", "psql"]
    isolation.isolate(tmp_path, runner, backup)
    assert dump.read_bytes() == original
    assert len(runner.calls) == 3
    assert json.loads((backup / "isolation.json").read_text())["complete"] is True


@pytest.mark.parametrize("fail", ["pg_dump", "pg_restore"])
def test_invalid_backup_never_reaches_database_mutation(tmp_path, fail):
    backup = root_layout(tmp_path)
    runner = DatabaseRunner(fail=fail)
    with pytest.raises(isolation.IsolationError) as error:
        isolation.isolate(tmp_path, runner, backup)
    assert "private-database-details" not in str(error.value)
    assert not any("psql" in cmd for cmd in runner.calls)
    assert not (backup / "isolation.json").exists()


def test_failed_sql_retry_reuses_verified_original_backup(tmp_path):
    backup = root_layout(tmp_path)
    failing = DatabaseRunner(fail="psql")
    with pytest.raises(isolation.IsolationError):
        isolation.isolate(tmp_path, failing, backup)
    digest = (backup / "before-test-isolation.dump").read_bytes()
    runner = DatabaseRunner()
    isolation.isolate(tmp_path, runner, backup)
    assert not any("pg_dump" in cmd for cmd in runner.calls)
    assert (backup / "before-test-isolation.dump").read_bytes() == digest


def test_tampered_backup_stops_retry(tmp_path):
    backup = root_layout(tmp_path)
    with pytest.raises(isolation.IsolationError):
        isolation.isolate(tmp_path, DatabaseRunner(fail="psql"), backup)
    (backup / "before-test-isolation.dump").write_bytes(b"PGDMPtampered")
    runner = DatabaseRunner()
    with pytest.raises(isolation.IsolationError):
        isolation.isolate(tmp_path, runner, backup)
    assert not runner.calls


def test_orphan_receipt_from_interruption_does_not_block_resume(tmp_path):
    backup = root_layout(tmp_path)
    for name in ("database-backup.pending", "isolation.pending"):
        (backup / name).write_bytes(b"interrupted private metadata")
        (backup / name).chmod(0o600)
    isolation.isolate(tmp_path, DatabaseRunner(), backup)
    assert json.loads((backup / "isolation.json").read_text())["complete"] is True


def test_sql_preserves_accounts_history_and_holds_old_external_work():
    db = sqlite3.connect(":memory:")
    db.executescript("""
      CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
      INSERT INTO users VALUES (1, 'test-account');
      CREATE TABLE platform_settings (key TEXT PRIMARY KEY, value TEXT);
      INSERT INTO platform_settings VALUES ('tracker_token', 'private'),
        ('emergency_cookie', 'private'), ('telegram_bot_token', 'private'),
        ('registration_shared_password', 'keep-encrypted'), ('ui_setting', 'keep');
      CREATE TABLE push_subscriptions (id TEXT PRIMARY KEY);
      INSERT INTO push_subscriptions VALUES ('old-device');
      CREATE TABLE sessions (id INTEGER PRIMARY KEY);
      INSERT INTO sessions VALUES (1);
      CREATE TABLE reliable_actions (id TEXT PRIMARY KEY, state TEXT, error_code TEXT, lease_until REAL);
      INSERT INTO reliable_actions VALUES ('queued', 'pending', NULL, 123),
        ('done', 'succeeded', NULL, NULL);
      CREATE TABLE task_messages (id TEXT PRIMARY KEY, sync_state TEXT);
      INSERT INTO task_messages VALUES ('queued', 'pending'), ('done', 'synced');
      CREATE TABLE notification_deliveries (id TEXT PRIMARY KEY, state TEXT, lease_owner TEXT, lease_until TEXT);
      INSERT INTO notification_deliveries VALUES ('queued', 'pending', 'worker', 'tomorrow'),
        ('done', 'succeeded', NULL, NULL);
      CREATE TABLE native_bot_control (id INTEGER PRIMARY KEY, queries_paused BOOLEAN, deliveries_paused BOOLEAN, revision INTEGER);
      CREATE TABLE native_bot_jobs (id TEXT PRIMARY KEY, enabled BOOLEAN);
      INSERT INTO native_bot_jobs VALUES ('scheduled', TRUE);
      CREATE TABLE native_bot_deliveries (id INTEGER PRIMARY KEY, state TEXT, error_code TEXT, lease_token_hash TEXT, lease_until TEXT, finished_at TEXT);
      INSERT INTO native_bot_deliveries VALUES (1, 'preparing', NULL, 'private-lease', 'tomorrow', NULL),
        (2, 'sent', NULL, NULL, NULL, 'yesterday');
      CREATE TABLE ai_automations (id INTEGER PRIMARY KEY, enabled BOOLEAN);
      CREATE TABLE ai_connectors (id INTEGER PRIMARY KEY, enabled BOOLEAN);
      CREATE TABLE ai_scripts (id INTEGER PRIMARY KEY, enabled BOOLEAN);
      INSERT INTO ai_automations VALUES (1, TRUE);
      INSERT INTO ai_connectors VALUES (1, TRUE);
      INSERT INTO ai_scripts VALUES (1, TRUE);
      CREATE TABLE ai_jobs (id INTEGER PRIMARY KEY, state TEXT, error TEXT);
      CREATE TABLE ai_actions (id INTEGER PRIMARY KEY, state TEXT, error TEXT);
      CREATE TABLE ai_runs (id INTEGER PRIMARY KEY, state TEXT, error TEXT);
      INSERT INTO ai_jobs VALUES (1, 'queued', NULL);
      INSERT INTO ai_actions VALUES (1, 'approved', NULL);
      INSERT INTO ai_runs VALUES (1, 'queued', NULL);
      CREATE TABLE host_operation_status (operation_id TEXT PRIMARY KEY, state TEXT,
        receipt_state TEXT, phase TEXT, error TEXT, progress_percent INTEGER,
        updated_at TEXT, terminal_at TEXT);
      INSERT INTO host_operation_status VALUES ('old-restore','running','accepted',
        'awaiting_host',NULL,10,'yesterday',NULL),
        ('old-success','succeeded','terminal','completed',NULL,100,'yesterday','yesterday');
    """)
    db.executescript(isolation.ISOLATION_SQL)
    db.executescript(isolation.ISOLATION_SQL)
    assert db.execute("SELECT * FROM users").fetchall() == [(1, "test-account")]
    assert db.execute("SELECT key FROM platform_settings ORDER BY key").fetchall() == [
        ("registration_shared_password",),
        ("ui_setting",),
    ]
    assert db.execute("SELECT count(*) FROM push_subscriptions").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM sessions").fetchone() == (0,)
    assert db.execute(
        "SELECT state,error_code,lease_until FROM reliable_actions WHERE id='queued'"
    ).fetchone() == ("needs_attention", "test_instance_quarantined", None)
    assert db.execute(
        "SELECT state FROM reliable_actions WHERE id='done'"
    ).fetchone() == ("succeeded",)
    assert db.execute(
        "SELECT sync_state FROM task_messages WHERE id='queued'"
    ).fetchone() == ("needs_attention",)
    assert db.execute(
        "SELECT state,lease_owner,lease_until FROM notification_deliveries WHERE id='queued'"
    ).fetchone() == ("expired", None, None)
    assert db.execute(
        "SELECT state FROM notification_deliveries WHERE id='done'"
    ).fetchone() == ("succeeded",)
    assert db.execute(
        "SELECT queries_paused, deliveries_paused FROM native_bot_control"
    ).fetchone() == (1, 1)
    assert db.execute("SELECT enabled FROM native_bot_jobs").fetchone() == (0,)
    assert db.execute(
        "SELECT state,lease_token_hash,lease_until FROM native_bot_deliveries WHERE id=1"
    ).fetchone() == ("failed", None, None)
    assert db.execute(
        "SELECT state FROM native_bot_deliveries WHERE id=2"
    ).fetchone() == ("sent",)
    for table in ("ai_automations", "ai_connectors", "ai_scripts"):
        assert db.execute(f"SELECT enabled FROM {table}").fetchone() == (0,)
    for table in ("ai_jobs", "ai_actions", "ai_runs"):
        assert db.execute(f"SELECT state FROM {table}").fetchone() == ("cancelled",)
    assert db.execute(
        "SELECT state,receipt_state,phase,error,progress_percent,terminal_at IS NOT NULL "
        "FROM host_operation_status WHERE operation_id='old-restore'"
    ).fetchone() == ("failed", "terminal", "failed", "request_superseded", None, 1)
    assert db.execute(
        "SELECT state,terminal_at FROM host_operation_status WHERE operation_id='old-success'"
    ).fetchone() == ("succeeded", "yesterday")
