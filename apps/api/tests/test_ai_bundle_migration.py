"""The bundle receipt schema upgrades existing local-AI databases without data loss."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from robopark_api.models import Park


def test_bundle_receipts_upgrade_from_local_ai_head(sqlite_database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", sqlite_database_url)
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    command.upgrade(config, "0057_local_ai")
    engine = create_engine(sqlite_database_url, future=True)
    with Session(engine) as db:
        park = Park(name="Migration", tag="retention-migration")
        db.add(park)
        db.flush()
        db.execute(
            text(
                "INSERT INTO ai_events (key, park_id, payload, occurred_at, processed, created_at) "
                "VALUES ('legacy-event', :park, :payload, 1, 1, 1)"
            ),
            {"park": park.id, "payload": '{"comment":"retained repair"}'},
        )
        db.commit()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai_documents "
                "(id, source_key, fingerprint, title, content, kind, state, trust, park_id, "
                "source_ref, revision, created_by, created_at, updated_at) VALUES "
                "('existing-doc', 'source-key', 'fingerprint', 'Title', 'Content', 'note', "
                "'active', 'unverified', NULL, 'public:repair-v1:00000000000000000000000000000001', "
                "1, NULL, 1, 1)"
            )
        )

    command.upgrade(config, "0058_ai_bundle_receipts")
    inspector = inspect(engine)
    assert {column["name"] for column in inspector.get_columns("ai_bundle_documents")} == {
        "source_ref",
        "document_id",
        "applied_document_revision",
        "applied_signature",
        "applied_bundle_revision",
        "seen_bundle_revision",
        "updated_at",
    }
    assert {
        index["name"]: index["column_names"]
        for index in inspector.get_indexes("ai_bundle_documents")
    } == {
        "ix_ai_bundle_documents_seen_bundle_revision": ["seen_bundle_revision"],
    }
    assert {
        index["name"]: index["column_names"] for index in inspector.get_indexes("ai_documents")
    }["ix_ai_documents_source_ref_park_id"] == ["source_ref", "park_id"]
    assert inspector.get_unique_constraints("ai_bundle_documents") == [
        {"name": "uq_ai_bundle_documents_document_id", "column_names": ["document_id"]}
    ]
    foreign_keys = inspector.get_foreign_keys("ai_bundle_documents")
    assert len(foreign_keys) == 1
    assert foreign_keys[0]["referred_table"] == "ai_documents"
    assert foreign_keys[0]["options"] == {"ondelete": "CASCADE"}
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT payload, payload_retained FROM ai_events WHERE key='legacy-event'")
        ).one() == ('{"comment":"retained repair"}', 1)
        assert connection.scalar(text("SELECT count(*) FROM ai_documents")) == 1
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0058_ai_bundle_receipts"
        )

    command.downgrade(config, "0057_local_ai")
    downgraded = inspect(engine)
    assert "ai_bundle_documents" not in downgraded.get_table_names()
    assert "ix_ai_documents_source_ref_park_id" not in {
        index["name"] for index in downgraded.get_indexes("ai_documents")
    }
    assert "payload_retained" not in {
        column["name"] for column in downgraded.get_columns("ai_events")
    }
