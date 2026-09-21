import json
from datetime import UTC, datetime

from robopark_api.services.release_status import release_status


def test_expired_lts_is_warning_not_operation_block(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    (public / "release-status.json").write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "channel": "stable",
                "support_class": "lts",
                "supported_until": "2026-01-01T00:00:00Z",
            }
        )
    )
    result = release_status(tmp_path, now=datetime(2026, 2, 1, tzinfo=UTC))
    assert result.support_status == "expired"
    assert result.operations_blocked is False


def test_missing_catalog_dates_are_unknown(tmp_path):
    (tmp_path / "public").mkdir()
    assert release_status(tmp_path).support_status == "unknown"
