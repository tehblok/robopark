from robopark_api.models import Base, Park, PlatformSetting


def test_metadata_has_platform_settings():
    assert "platform_settings" in Base.metadata.tables


def test_park_has_tracker_fields():
    cols = {c.name for c in Park.__table__.columns}
    assert {"tracker_queue", "group_id", "chat_id", "feature_blockers"} <= cols


def test_platform_setting_columns():
    cols = {c.name for c in PlatformSetting.__table__.columns}
    assert {"key", "value", "updated_at"} <= cols
