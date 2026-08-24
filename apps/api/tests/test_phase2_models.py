from sqlalchemy import inspect

from robopark_api.models import (
    AccessStatus,
    Base,
    Park,
    ParkRequest,
    User,
    UserPark,
)


def test_metadata_has_phase2_tables():
    names = set(Base.metadata.tables)
    assert {"parks", "user_parks", "park_requests", "users"} <= names


def test_user_has_access_status_column():
    column = User.__table__.columns["access_status"]

    assert column.type.length == 32
    assert column.default.arg == AccessStatus.approved.value


def test_access_status_values_are_locked():
    assert [status.value for status in AccessStatus] == [
        "pending",
        "approved",
        "rejected",
    ]


def test_phase2_models_have_required_columns_and_foreign_keys():
    assert set(Park.__table__.columns.keys()) == {
        "id",
        "name",
        "tag",
        "is_active",
        "created_at",
        "tracker_queue",
        "tracker_priority",
        "tracker_type",
        "group_id",
        "chat_id",
        "feature_reports",
        "feature_blockers",
        "feature_sla_repair",
        "feature_backlog_alerts",
    }
    assert set(UserPark.__table__.columns.keys()) == {"user_id", "park_id"}
    assert set(ParkRequest.__table__.columns.keys()) == {
        "id",
        "user_id",
        "park_id",
        "status",
        "created_at",
        "resolved_at",
        "resolved_by",
    }
    assert ParkRequest.__table__.columns["status"].default.arg == "pending"
    assert {
        foreign_key.target_fullname
        for foreign_key in ParkRequest.__table__.foreign_keys
    } == {"users.id", "parks.id"}


def test_user_exposes_park_relationships():
    relationships = inspect(User).relationships

    assert relationships["parks"].secondary is UserPark.__table__
    assert relationships["park_requests"].mapper.class_ is ParkRequest


def test_seed_royal_is_approved(seed_royal):
    assert seed_royal.access_status == AccessStatus.approved.value
