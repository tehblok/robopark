import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import BigInteger, LargeBinary, create_engine, inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from robopark_api import models, notification_delivery_models, schedule_models  # noqa: F401
from robopark_api.models import (
    AuthSession,
    Base,
    InventoryCatalogComponent,
    InventoryCatalogPart,
    InventoryCount,
    InventoryCountLine,
    InventoryMovement,
    InventoryParkStock,
    InventoryPart,
    InventoryReceiptLine,
    User,
)
from robopark_api.task_workflow_models import ReliableAction


def test_metadata_has_required_tables():
    assert set(Base.metadata.tables) == {
        "users",
        "sessions",
        "parks",
        "user_parks",
        "park_requests",
        "platform_settings",
        "emergency_sections",
        "emergency_fields",
        "emergency_section_roles",
        "emergency_readings",
        "park_blocker_history",
        "reports",
        "report_attachments",
        "audit_log",
        "permissions",
        "roles",
        "role_permissions",
        "user_permissions",
        "analytics_snapshots",
        "analytics_observations",
        "diagnostic_rules",
        "diagnostic_unknowns",
        "diagnostic_unknown_sightings",
        "tracker_presence",
        "reliable_actions",
        "task_messages",
        "task_attachments",
        "task_reviews",
        "hidden_tasks",
        "tracker_handoffs",
        "tracker_claims",
        "campaigns",
        "campaign_parks",
        "campaign_submissions",
        "campaign_snapshot_tickets",
        "inventory_components",
        "inventory_parts",
        "inventory_movements",
        "inventory_catalog_components",
        "inventory_catalog_parts",
        "inventory_park_stocks",
        "inventory_photo_cleanup",
        "inventory_receipts",
        "inventory_receipt_lines",
        "inventory_counts",
        "inventory_count_lines",
        "inventory_migration_conflicts",
        "offline_sync_receipts",
        "media_upload_sessions",
        "schedule_entries",
        "push_subscriptions",
        "notification_preferences",
        "notification_events",
        "notification_deliveries",
        "tracker_notification_cursors",
        "system_incident_occurrences",
        "auth_throttle_states",
        "privileged_credentials",
        "privileged_recovery_codes",
        "privileged_reauthorizations",
        "privileged_auth_audit",
        "ip_geo_cache",
        "ip_geo_quota",
    }


def test_global_inventory_accumulators_compile_as_postgresql_bigint():
    columns = [
        InventoryParkStock.quantity,
        InventoryParkStock.minimum_quantity,
        InventoryParkStock.version,
        InventoryPart.quantity,
        InventoryPart.minimum_quantity,
        InventoryMovement.delta,
        InventoryMovement.balance_before,
        InventoryMovement.balance_after,
        InventoryReceiptLine.quantity,
        InventoryCountLine.expected_quantity,
        InventoryCountLine.actual_quantity,
        InventoryCountLine.difference,
    ]

    assert all(isinstance(column.type, BigInteger) for column in columns)
    assert all(column.type.compile(dialect=postgresql.dialect()) == "BIGINT" for column in columns)
    assert isinstance(InventoryCount.normalized_name_key.type, LargeBinary)
    assert InventoryCount.normalized_name_key.type.compile(dialect=postgresql.dialect()).startswith(
        "BYTEA"
    )
    assert InventoryCatalogComponent.normalized_name.type.length >= 384
    assert InventoryCatalogPart.normalized_name.type.length >= 384
    assert InventoryCatalogPart.normalized_article.type.length >= 384


def test_alembic_head_is_privileged_auth():
    api_dir = Path(__file__).parents[1]
    script = ScriptDirectory.from_config(Config(api_dir / "alembic.ini"))
    assert script.get_heads() == ["0045_privileged_recovery_hashes"]


def test_privileged_audit_is_immutable_after_sqlite_migration(
    sqlite_database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "head")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO privileged_auth_audit "
                "(action, outcome, actor_username, actor_role) "
                "VALUES ('test', 'denied', 'royal', 'royal')"
            )
        )
    with (
        pytest.raises(DBAPIError, match="privileged_auth_audit_immutable"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE privileged_auth_audit SET outcome = 'success'"))
    with (
        pytest.raises(DBAPIError, match="privileged_auth_audit_immutable"),
        engine.begin() as connection,
    ):
        connection.execute(text("DELETE FROM privileged_auth_audit"))


def test_privileged_audit_postgresql_migration_emits_update_delete_trigger():
    api_dir = Path(__file__).parents[1]
    script = ScriptDirectory.from_config(Config(api_dir / "alembic.ini"))
    module = script.get_revision("0044_privileged_auth").module
    statements = []

    class FakeBind:
        class dialect:
            name = "postgresql"

    class FakeOp:
        get_bind = staticmethod(lambda: FakeBind())
        create_table = staticmethod(lambda *args, **kwargs: None)
        create_index = staticmethod(lambda *args, **kwargs: None)
        execute = staticmethod(statements.append)

    original = module.op
    module.op = FakeOp()
    try:
        module.upgrade()
    finally:
        module.op = original
    ddl = " ".join(statements)
    assert "BEFORE UPDATE OR DELETE" in ddl
    assert "RAISE EXCEPTION 'privileged_auth_audit_immutable'" in ddl


def test_privileged_recovery_hash_migration_labels_legacy_rows(
    sqlite_database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0044_privileged_auth")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO privileged_recovery_codes (id, user_id, code_hash) "
                "VALUES (1, 999, 'legacy-keyed-digest')"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT hash_version FROM privileged_recovery_codes WHERE id = 1")
        ) == "legacy-hmac-v1"


def test_sync_closure_scan_migration_adds_review_index(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0038_inventory_photo_cleanup")
    command.upgrade(config, "head")
    inspector = inspect(create_engine(sqlite_database_url, future=True))
    indexes = {index["name"]: index["column_names"] for index in inspector.get_indexes("task_reviews")}
    assert indexes["ix_task_reviews_closure_scan"] == ["state", "closed_at", "issue_key"]


def test_notification_delivery_migration_upgrades_linear_head(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0039_sync_closure_scan")
    command.upgrade(config, "head")
    engine = create_engine(sqlite_database_url, future=True)
    inspector = inspect(engine)
    assert "notification_deliveries" in inspector.get_table_names()
    assert {column["name"] for column in inspector.get_columns("notification_deliveries")} >= {
        "event_id", "channel", "state", "attempts", "next_attempt_at", "expires_at",
        "idempotency_key", "lease_owner", "lease_until",
    }
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0045_privileged_recovery_hashes"


def test_schedule_series_lookup_index_is_used(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0042_user_timezone")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO schedule_entries "
            "(id, owner_user_id, park_id, kind, start_at, end_at, source, series_id, "
            "created_by_user_id, updated_by_user_id) VALUES "
            "('kept-series-row', 1, 1, 'shift', '2026-01-01 09:00:00', "
            "'2026-01-01 21:00:00', 'self', 'series', 1, 1)"
        ))
    command.upgrade(config, "head")
    inspector = inspect(engine)
    indexes = {item["name"]: item["column_names"] for item in inspector.get_indexes("schedule_entries")}
    assert indexes["ix_schedule_owner_series_end"] == ["owner_user_id", "series_id", "end_at"]
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM schedule_entries WHERE id = 'kept-series-row'"
        )) == 1
        plan = connection.execute(text(
            "EXPLAIN QUERY PLAN SELECT id FROM schedule_entries "
            "WHERE owner_user_id = 1 AND series_id = 'series' AND end_at > '2026-01-01' LIMIT 1"
        )).all()
    assert "ix_schedule_owner_series_end" in " ".join(str(row) for row in plan)
    command.downgrade(config, "0042_user_timezone")
    inspector = inspect(engine)
    assert "ix_schedule_owner_series_end" not in {
        item["name"] for item in inspector.get_indexes("schedule_entries")
    }
    with engine.connect() as connection:
        assert connection.scalar(text(
            "SELECT count(*) FROM schedule_entries WHERE id = 'kept-series-row'"
        )) == 1


def test_inventory_photo_cleanup_migration_is_additive(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0037_claim_workflow_visibility")
    command.upgrade(config, "head")
    inspector = inspect(create_engine(sqlite_database_url, future=True))

    assert {
        "storage_key",
        "attempts",
        "last_error",
        "created_at",
        "updated_at",
    } == {column["name"] for column in inspector.get_columns("inventory_photo_cleanup")}


def test_claim_and_message_visibility_columns(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0036_audit_remediation_state")
    engine = create_engine(sqlite_database_url, future=True)

    with engine.begin() as connection:
        role_id = connection.execute(text("SELECT id FROM roles ORDER BY id LIMIT 1")).scalar_one()
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role_id, access_status, "
                "must_change_password, is_active) "
                "VALUES (1, 'workflow_user', 'hash', :role_id, 'approved', 0, 1)"
            ),
            {"role_id": role_id},
        )
        connection.execute(
            text("INSERT INTO parks (id, name, tag, is_active) VALUES (1, 'Park', 'park', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO tracker_claims "
                "(issue_key, park_id, owner_user_id, updated_by_user_id, updated_at) "
                "VALUES ('SDCFLEETOPS-1', 1, 1, 1, 1.0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO task_messages "
                "(id, issue_key, kind, author_name, text, sync_state, created_at, updated_at) "
                "VALUES ('message-1', 'SDCFLEETOPS-1', 'system', 'Robopark', "
                "'Claimed', 'saved', 1.0, 1.0)"
            )
        )

    command.upgrade(config, "head")
    inspector = inspect(engine)
    claim = {column["name"] for column in inspector.get_columns("tracker_claims")}
    message = {column["name"] for column in inspector.get_columns("task_messages")}
    assert {"state", "start_action_id", "operator_user_id"} <= claim
    assert "visibility" in message

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT state FROM tracker_claims WHERE issue_key = 'SDCFLEETOPS-1'")
        ).scalar_one() == "active"
        assert connection.execute(
            text("SELECT visibility FROM task_messages WHERE id = 'message-1'")
        ).scalar_one() == "participants"

    claim_foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key
        for foreign_key in inspector.get_foreign_keys("tracker_claims")
    }
    assert claim_foreign_keys[("start_action_id",)]["referred_table"] == "reliable_actions"
    assert claim_foreign_keys[("start_action_id",)]["options"]["ondelete"] == "SET NULL"
    assert claim_foreign_keys[("operator_user_id",)]["referred_table"] == "users"
    assert claim_foreign_keys[("operator_user_id",)]["options"]["ondelete"] == "SET NULL"


def test_audit_remediation_models_support_atomic_claims_and_bounded_cleanup():
    tracker_notification_cursor = schedule_models.TrackerNotificationCursor
    system_incident_occurrence = schedule_models.SystemIncidentOccurrence
    auth_throttle_state = models.AuthThrottleState
    assert set(tracker_notification_cursor.__table__.columns.keys()) == {
        "scope_key",
        "cursor_value",
        "lease_owner",
        "lease_until",
        "last_success_at",
        "last_error",
        "created_at",
        "updated_at",
    }
    assert {index.name for index in tracker_notification_cursor.__table__.indexes} == {
        "ix_tracker_notification_lease"
    }

    incident_indexes = {
        index.name: index for index in system_incident_occurrence.__table__.indexes
    }
    assert set(incident_indexes) == {
        "uq_system_incident_active_key",
        "ix_system_incident_cleanup",
    }
    assert incident_indexes["uq_system_incident_active_key"].unique
    assert (
        str(incident_indexes["uq_system_incident_active_key"].dialect_options["postgresql"]["where"])
        == "resolved_at IS NULL"
    )

    assert set(auth_throttle_state.__table__.columns.keys()) == {
        "key_hash",
        "failure_count",
        "window_started_at",
        "locked_until",
        "expires_at",
        "created_at",
        "updated_at",
    }
    assert "key" not in auth_throttle_state.__table__.columns
    assert {index.name for index in auth_throttle_state.__table__.indexes} == {
        "ix_auth_throttle_expiry"
    }


def test_audit_remediation_state_upgrade_and_downgrade(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0035_schedules_and_push")
    engine = create_engine(sqlite_database_url, future=True)

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert {
        "tracker_notification_cursors",
        "system_incident_occurrences",
        "auth_throttle_states",
    } <= set(inspector.get_table_names())
    assert {index["name"] for index in inspector.get_indexes("tracker_notification_cursors")} == {
        "ix_tracker_notification_lease"
    }
    assert {index["name"] for index in inspector.get_indexes("system_incident_occurrences")} == {
        "uq_system_incident_active_key",
        "ix_system_incident_cleanup",
    }
    assert {index["name"] for index in inspector.get_indexes("auth_throttle_states")} == {
        "ix_auth_throttle_expiry"
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("auth_throttle_states")
    } == {"ck_auth_throttle_failure_count", "ck_auth_throttle_key_hash"}
    assert compare_metadata(MigrationContext.configure(engine.connect()), Base.metadata) == []

    command.downgrade(config, "0035_schedules_and_push")
    assert not {
        "tracker_notification_cursors",
        "system_incident_occurrences",
        "auth_throttle_states",
    } & set(inspect(engine).get_table_names())


def test_clean_alembic_process_registers_audit_remediation_metadata(tmp_path):
    api_dir = Path(__file__).parents[1]
    database_url = f"sqlite:///{tmp_path / 'clean-alembic.db'}"
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "PYTHONPATH": str(api_dir / "src"),
    }
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=api_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    checked = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "check"],
        cwd=api_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_alembic_revision_ids_fit_version_table_column():
    api_dir = Path(__file__).parents[1]
    script = ScriptDirectory.from_config(Config(api_dir / "alembic.ini"))
    assert {
        revision.revision: len(revision.revision)
        for revision in script.walk_revisions()
        if len(revision.revision) > 32
    } == {}


def test_campaign_snapshot_upgrade_preserves_legacy_selection_and_indexes(
    sqlite_database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0028_reliable_task_workflow")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        role_id = connection.execute(text("SELECT id FROM roles ORDER BY id LIMIT 1")).scalar_one()
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role_id, access_status, "
                "must_change_password, is_active) "
                "VALUES (1, 'campaign_admin', 'hash', :role_id, 'approved', 0, 1)"
            ),
            {"role_id": role_id},
        )
        connection.execute(
            text("INSERT INTO parks (id, name, tag, is_active) VALUES (1, 'Park', 'park', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO campaigns "
                "(id, kind, name, tracker_tag, starts_on, due_on, created_by) "
                "VALUES (1, 'wrapping', 'Existing', 'legacy-tag', '2026-09-01', '2026-10-01', 1)"
            )
        )

    command.upgrade(config, "head")
    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT selection_mode, rule_revision, snapshot_state, snapshot_at "
                "FROM campaigns WHERE id = 1"
            )
        ).one() == ("tag", 1, "idle", None)
        connection.execute(
            text(
                "INSERT INTO campaign_snapshot_tickets "
                "(campaign_id, issue_key, park_id, summary, status, rule_revision) "
                "VALUES (1, 'TEST-1', 1, 'Replace wrap', 'Open', 1)"
            )
        )
        assert (
            connection.execute(
                text("SELECT issue_key FROM campaign_snapshot_tickets WHERE campaign_id = 1")
            ).scalar_one()
            == "TEST-1"
        )
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []

    inspector = inspect(engine)
    assert "campaign_snapshot_tickets" in inspector.get_table_names()
    assert "ix_campaign_snapshot_campaign_revision_park" in {
        index["name"] for index in inspector.get_indexes("campaign_snapshot_tickets")
    }
    assert any(
        constraint["name"] == "uq_campaign_snapshot_issue"
        and constraint["column_names"] == ["campaign_id", "issue_key"]
        for constraint in inspector.get_unique_constraints("campaign_snapshot_tickets")
    )
    assert any(
        fk["referred_table"] == "campaigns" and fk["options"].get("ondelete") == "CASCADE"
        for fk in inspector.get_foreign_keys("campaign_snapshot_tickets")
    )

    command.downgrade(config, "0028_reliable_task_workflow")
    assert "campaign_snapshot_tickets" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT tracker_tag FROM campaigns WHERE id = 1")).scalar_one()
            == "legacy-tag"
        )


def test_reliable_workflow_upgrade_and_downgrade_preserve_legacy_submissions(
    sqlite_database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0027_emergency_readings")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        role_id = connection.execute(text("SELECT id FROM roles ORDER BY id LIMIT 1")).scalar_one()
        connection.execute(
            text(
                "INSERT INTO users (id, username, password_hash, role_id, access_status, "
                "must_change_password, is_active) "
                "VALUES (1, 'worker', 'hash', :role_id, 'approved', 0, 1)"
            ),
            {"role_id": role_id},
        )
        connection.execute(
            text(
                "INSERT INTO tracker_submissions "
                "(id, actor_id, issue_key, action, request_key, payload_hash, state, "
                "result_json, created_at) VALUES "
                "(7, 1, 'SDCFLEETOPS-1', 'claim', 'request-0001', 'abc123', "
                "'succeeded', :result_json, 42.0)"
            ),
            {"result_json": '{"ok":true}'},
        )

    command.upgrade(config, "head")
    assert "tracker_submissions" not in inspect(engine).get_table_names()
    action_column = next(
        column
        for column in inspect(engine).get_columns("reliable_actions")
        if column["name"] == "action"
    )
    assert action_column["type"].length == 32
    with Session(engine) as session:
        action = session.scalar(select(ReliableAction))
        assert action is not None
        assert action.resource_type == "tracker_issue"
        assert action.resource_id == "SDCFLEETOPS-1"
        assert action.state == "succeeded"
        assert action.idempotency_key == "request-0001"
        assert action.result_json == '{"ok":true}'

    command.downgrade(config, "0027_emergency_readings")
    assert "reliable_actions" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        restored = connection.execute(
            text(
                "SELECT actor_id, issue_key, action, request_key, payload_hash, state, "
                "result_json, created_at FROM tracker_submissions"
            )
        ).one()
    assert tuple(restored) == (
        1,
        "SDCFLEETOPS-1",
        "claim",
        "request-0001",
        "abc123",
        "succeeded",
        '{"ok":true}',
        42.0,
    )


def test_emergency_readings_upgrade_from_previous_head(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]
    config = Config(api_dir / "alembic.ini")

    command.upgrade(config, "0026_global_inventory_workflows")
    engine = create_engine(sqlite_database_url, future=True)
    assert "emergency_readings" not in inspect(engine).get_table_names()

    command.upgrade(config, "0027_emergency_readings")

    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("emergency_readings")} == {
        "id",
        "section_id",
        "path",
        "label",
        "display_kind",
        "unit",
        "precision",
        "enabled_path",
        "no_data_json",
        "warning_below",
        "warning_above",
        "critical_below",
        "critical_above",
        "view",
        "x",
        "y",
        "label_direction",
        "is_enabled",
        "sort_order",
    }
    assert any(
        constraint["name"] == "uq_emergency_readings_section_path"
        and constraint["column_names"] == ["section_id", "path"]
        for constraint in inspector.get_unique_constraints("emergency_readings")
    )
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("emergency_readings")
    } == {
        "ck_emergency_readings_display_kind",
        "ck_emergency_readings_label_direction",
        "ck_emergency_readings_precision",
        "ck_emergency_readings_view",
        "ck_emergency_readings_x",
        "ck_emergency_readings_y",
    }
    assert any(
        index["name"] == "ix_emergency_readings_sort_order_id"
        and index["column_names"] == ["sort_order", "id"]
        and not index["unique"]
        for index in inspector.get_indexes("emergency_readings")
    )
    foreign_keys = inspector.get_foreign_keys("emergency_readings")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["section_id"]
    assert foreign_keys[0]["referred_table"] == "emergency_sections"
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"

    command.downgrade(config, "0026_global_inventory_workflows")
    assert "emergency_readings" not in inspect(engine).get_table_names()


def test_emergency_reading_model_matches_catalog_contract():
    assert hasattr(models, "EmergencyReading")
    emergency_reading = models.EmergencyReading
    assert set(emergency_reading.__table__.columns.keys()) == {
        "id",
        "section_id",
        "path",
        "label",
        "display_kind",
        "unit",
        "precision",
        "enabled_path",
        "no_data_json",
        "warning_below",
        "warning_above",
        "critical_below",
        "critical_above",
        "view",
        "x",
        "y",
        "label_direction",
        "is_enabled",
        "sort_order",
    }


def test_diagnostic_rules_upgrade_from_previous_head(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]
    config = Config(api_dir / "alembic.ini")

    command.upgrade(config, "0018_analytics_observations")
    engine = create_engine(sqlite_database_url, future=True)
    assert "diagnostic_rules" not in inspect(engine).get_table_names()

    command.upgrade(config, "0019_diagnostic_rules")

    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("diagnostic_rules")} == {
        "id",
        "source_path",
        "match_kind",
        "pattern",
        "example",
        "title",
        "description",
        "severity",
        "part",
        "preferred_view",
        "x",
        "y",
        "indicator",
        "is_enabled",
        "sort_order",
    }
    assert all(not column["nullable"] for column in inspector.get_columns("diagnostic_rules"))
    assert any(
        constraint["name"] == "uq_diagnostic_rules_source_match_pattern"
        and constraint["column_names"] == ["source_path", "match_kind", "pattern"]
        for constraint in inspector.get_unique_constraints("diagnostic_rules")
    )
    assert any(
        index["name"] == "ix_diagnostic_rules_sort_order_id"
        and index["column_names"] == ["sort_order", "id"]
        and not index["unique"]
        for index in inspector.get_indexes("diagnostic_rules")
    )
    assert {
        constraint["name"] for constraint in inspector.get_check_constraints("diagnostic_rules")
    } == {
        "ck_diagnostic_rules_indicator",
        "ck_diagnostic_rules_match_kind",
        "ck_diagnostic_rules_preferred_view",
        "ck_diagnostic_rules_severity",
        "ck_diagnostic_rules_x",
        "ck_diagnostic_rules_y",
    }

    command.downgrade(config, "0018_analytics_observations")
    assert "diagnostic_rules" not in inspect(engine).get_table_names()


def test_analytics_upgrade_and_downgrade_preserve_existing_history(
    sqlite_database_url, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0017_driver_work_reports")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO parks (id, name, tag, is_active) VALUES (1, 'Test', 'test', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO park_blocker_history (park_id, bucket_start, arrived_count, departed_count, definition_version) VALUES (1, '2026-09-01 00:00:00', 4, 9, 2)"
            )
        )
    command.upgrade(config, "0018_analytics_observations")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO analytics_snapshots (park_id, bucket_start, observed_at) VALUES (1, '2026-09-01 00:00:00', '2026-09-01 00:12:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO analytics_observations (park_id, bucket_start, issue_key, status, status_bucket) VALUES (1, '2026-09-01 00:00:00', 'RP-1', 'new', 'new')"
            )
        )
    command.downgrade(config, "0017_driver_work_reports")
    assert "analytics_snapshots" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert tuple(
            connection.execute(
                text("SELECT arrived_count, departed_count FROM park_blocker_history")
            ).one()
        ) == (4, 9)
    command.upgrade(config, "head")
    assert "analytics_observations" in inspect(engine).get_table_names()


def test_history_migration_preserves_legacy_definition(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0015_report_attachments")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO parks (id, name, tag, is_active) VALUES (1, 'Test', 'test', 1)")
        )
        connection.execute(
            text(
                "INSERT INTO park_blocker_history (park_id, bucket_start, arrived_count, departed_count) VALUES (1, '2026-09-01 00:00:00', 4, 9)"
            )
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT definition_version, arrived_count, departed_count FROM park_blocker_history"
            )
        ).one()
    assert tuple(row) == (1, 4, 9)


def test_migrated_report_attachments_contract(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]
    command.upgrade(Config(api_dir / "alembic.ini"), "head")
    inspector = inspect(create_engine(sqlite_database_url, future=True))

    assert {column["name"] for column in inspector.get_columns("report_attachments")} == {
        "id",
        "report_id",
        "kind",
        "filename",
        "content_type",
        "size_bytes",
        "storage_key",
        "created_at",
    }
    foreign_keys = inspector.get_foreign_keys("report_attachments")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["report_id"]
    assert foreign_keys[0]["referred_table"] == "reports"
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"
    assert any(
        constraint["column_names"] == ["report_id", "kind"]
        for constraint in inspector.get_unique_constraints("report_attachments")
    )
    assert any(
        index["column_names"] == ["report_id"]
        for index in inspector.get_indexes("report_attachments")
    )


def test_models_match_required_schema():
    assert set(User.__table__.columns.keys()) == {
        "id",
        "username",
        "password_hash",
        "role_id",
        "access_status",
        "tracker_login",
        "must_change_password",
        "is_active",
        "created_at",
        "last_seen_at",
        "last_ip",
        "last_device",
        "last_location",
    }
    assert set(AuthSession.__table__.columns.keys()) == {
        "id",
        "user_id",
        "token_hash",
        "expires_at",
        "created_at",
    }
    assert AuthSession.__tablename__ == "sessions"
    assert {foreign_key.target_fullname for foreign_key in AuthSession.__table__.foreign_keys} == {
        "users.id"
    }


def test_create_all_builds_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "metadata.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    from robopark_api.config import Settings

    engine = create_engine(Settings().database_url, future=True)
    Base.metadata.create_all(engine)

    assert set(inspect(engine).get_table_names()) >= {"users", "sessions"}


def test_alembic_upgrade_with_percent_in_database_url(tmp_path, monkeypatch):
    db_path = tmp_path / "user%40data.db"
    database_url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    api_dir = Path(__file__).parents[1]
    config = Config(api_dir / "alembic.ini")

    command.upgrade(config, "head")

    engine = create_engine(database_url, future=True)
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "users",
        "sessions",
    }


def test_alembic_upgrade_builds_schema(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]
    config = Config(api_dir / "alembic.ini")

    command.upgrade(config, "head")

    engine = create_engine(sqlite_database_url, future=True)
    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "users",
        "sessions",
    }


def test_migrated_schema_matches_models(sqlite_database_url, monkeypatch):
    """A model change without a matching migration must fail here, not on deploy."""
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]

    command.upgrade(Config(api_dir / "alembic.ini"), "head")

    engine = create_engine(sqlite_database_url, future=True)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        difference = compare_metadata(context, Base.metadata)

    assert difference == []


def test_migrated_indexes_and_foreign_keys_match_models(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]

    command.upgrade(Config(api_dir / "alembic.ini"), "head")

    inspector = inspect(create_engine(sqlite_database_url, future=True))

    unique_indexes = {
        (table, index["name"])
        for table in ("users", "sessions")
        for index in inspector.get_indexes(table)
        if index["unique"]
    }
    assert unique_indexes == {
        ("users", "ix_users_username"),
        ("sessions", "ix_sessions_token_hash"),
    }

    foreign_keys = inspector.get_foreign_keys("sessions")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["constrained_columns"] == ["user_id"]
    assert foreign_keys[0]["referred_table"] == "users"
    assert foreign_keys[0]["referred_columns"] == ["id"]
    assert foreign_keys[0]["options"]["ondelete"] == "CASCADE"


def test_migrated_parks_have_tracker_columns_and_history_table(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    api_dir = Path(__file__).parents[1]

    command.upgrade(Config(api_dir / "alembic.ini"), "head")

    inspector = inspect(create_engine(sqlite_database_url, future=True))
    park_columns = {column["name"] for column in inspector.get_columns("parks")}
    assert {"tracker_priority", "tracker_type"} <= park_columns
    assert "park_blocker_history" in inspector.get_table_names()

    history_columns = {column["name"] for column in inspector.get_columns("park_blocker_history")}
    assert history_columns == {
        "id",
        "park_id",
        "bucket_start",
        "arrived_count",
        "departed_count",
        "scanned_at",
        "definition_version",
    }

    unique_indexes = {
        index["name"] for index in inspector.get_indexes("park_blocker_history") if index["unique"]
    }
    unique_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("park_blocker_history")
    }
    assert (
        "uq_park_blocker_history_park_bucket" in unique_indexes
        or "uq_park_blocker_history_park_bucket" in unique_constraints
    )


def test_unknown_upgrade_is_additive(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0019_diagnostic_rules")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO parks (id, name, tag, is_active) VALUES (1, 'Test', 'test', 1)")
        )
    command.upgrade(config, "0020_diagnostic_unknowns")
    assert {"diagnostic_unknowns", "diagnostic_unknown_sightings"} <= set(
        inspect(engine).get_table_names()
    )
    with engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM parks WHERE id=1")).scalar_one() == "Test"
    command.downgrade(config, "0019_diagnostic_rules")
    assert "diagnostic_unknowns" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM parks WHERE id=1")).scalar_one() == "Test"


def test_original_unit_upgrade_leaves_legacy_samples_unverified(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0020_diagnostic_unknowns")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO diagnostic_unknowns (identity, source_path, source_segments_json, raw_json, first_seen_at, last_seen_at, observations, last_robot, state) VALUES ('legacy', 'errors', '[\"errors\"]', '\"TARGET\"', '2026-09-01', '2026-09-01', 1, 'robot', 'new')"
            )
        )
    command.upgrade(config, "0021_diagnostic_unknown_original")
    with engine.connect() as connection:
        assert tuple(
            connection.execute(
                text("SELECT raw_json, original_json, state FROM diagnostic_unknowns")
            ).one()
        ) == ('"TARGET"', None, "new")
    command.downgrade(config, "0020_diagnostic_unknowns")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT raw_json FROM diagnostic_unknowns")).scalar_one()
            == '"TARGET"'
        )


def test_inventory_upgrade_preserves_existing_data(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0021_diagnostic_unknown_original")
    engine = create_engine(sqlite_database_url, future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO parks (id, name, tag, is_active) VALUES (1, 'Existing', 'existing', 1)"
            )
        )
    command.upgrade(config, "head")
    assert {
        "tracker_presence",
        "reliable_actions",
        "task_messages",
        "task_attachments",
        "task_reviews",
        "hidden_tasks",
        "tracker_handoffs",
        "campaigns",
        "campaign_parks",
        "campaign_submissions",
        "inventory_components",
        "inventory_parts",
        "inventory_movements",
        "tracker_claims",
        "inventory_catalog_components",
        "inventory_catalog_parts",
        "inventory_park_stocks",
        "inventory_receipts",
        "inventory_receipt_lines",
        "inventory_counts",
        "inventory_count_lines",
        "inventory_migration_conflicts",
    } <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT name FROM parks WHERE id=1")).scalar_one() == "Existing"
        )
        count_columns = {
            column["name"] for column in inspect(connection).get_columns("inventory_counts")
        }
        count_indexes = {
            index["name"] for index in inspect(connection).get_indexes("inventory_counts")
        }
        assert "normalized_name" in count_columns
        assert "normalized_name_key" in count_columns
        assert {
            "ix_inventory_counts_park_name_key_id",
            "ix_inventory_counts_park_created_id",
        } <= count_indexes
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(config, "0021_diagnostic_unknown_original")
    assert "campaigns" not in inspect(engine).get_table_names()
    assert "inventory_parts" not in inspect(engine).get_table_names()
    assert "tracker_submissions" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT name FROM parks WHERE id=1")).scalar_one() == "Existing"
        )
