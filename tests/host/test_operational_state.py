import json
from datetime import UTC, datetime

from robopark_host.checks import DiagnosticReport
from robopark_host.commands import publish_health
from robopark_host.doctor import _release_metadata, _state_check
from robopark_host.state import atomic_write_json


def test_real_updater_status_and_rc_release_are_visible(host_paths):
    atomic_write_json(
        host_paths.current / "manifest.json", {"app_version": "1.2.3-rc.2", "git_sha": "a" * 40}
    )
    atomic_write_json(host_paths.ops / "public/host-status.json", {"state": "previous_restored"})
    atomic_write_json(host_paths.state / "updater-journal.json", {"phase": "rolled_back"})
    assert _state_check(host_paths, "updater", "checked").status == "ok"
    publish_health(host_paths, DiagnosticReport([]))
    public = json.loads((host_paths.ops / "public/system-health.json").read_text())
    assert public["version"] == "1.2.3-rc.2"
    assert public["update"]["state"] == "rolled_back"
    assert _release_metadata(host_paths)["version"] == "1.2.3-rc.2"


def test_backup_reader_accepts_real_api_receipt_without_echoing_extra_fields(host_paths):
    atomic_write_json(
        host_paths.var / "api-ops/last-backup.json",
        {
            "status": "success",
            "completed_at": datetime.now(UTC).isoformat(),
            "token": "LEAK",
        },
    )
    publish_health(host_paths, DiagnosticReport([]))
    public = (host_paths.ops / "public/system-health.json").read_text()
    assert json.loads(public)["last_backup"]["status"] == "success"
    assert "LEAK" not in public
    assert _state_check(host_paths, "backup", "checked").status == "ok"


def test_real_manual_recovery_state_is_failed(host_paths):
    atomic_write_json(
        host_paths.ops / "public/host-status.json",
        {
            "state": "maintenance",
            "error": "manual_recovery_required",
        },
    )
    assert _state_check(host_paths, "updater", "checked").status == "failed"


def test_actual_journal_failure_is_not_reported_as_unknown(host_paths):
    atomic_write_json(host_paths.state / "updater-journal.json", {"phase": "failed"})
    assert _state_check(host_paths, "updater", "checked").status == "failed"


def test_record_host_backup_is_read_by_real_public_projection(host_paths):
    from robopark_host.operational_state import record_backup

    record_backup(host_paths, "success")
    publish_health(host_paths, DiagnosticReport([]))
    public = json.loads((host_paths.ops / "public/system-health.json").read_text())
    assert public["last_backup"]["status"] == "success"
    assert _state_check(host_paths, "backup", "checked").status == "ok"


def test_malformed_operational_fields_fail_closed_without_crashing_doctor(host_paths):
    from robopark_host.operational_state import update_state

    atomic_write_json(host_paths.ops / "public/host-status.json", {"state": []})
    assert update_state(host_paths)["state"] == "unknown"
    assert _state_check(host_paths, "updater", "checked").status != "ok"
    (host_paths.ops / "public/host-status.json").unlink()
    atomic_write_json(host_paths.state / "updater-journal.json", {"phase": []})
    assert _state_check(host_paths, "updater", "checked").status != "ok"
