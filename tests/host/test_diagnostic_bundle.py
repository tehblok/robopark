import json
import zipfile

import pytest
from robopark_host.bundle import create_diagnostic_bundle
from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport


class BundleRunner:
    def __call__(self, command, *, timeout, max_output):
        if command[0] == "journalctl":
            return CommandResult(stdout="TUNA_TOKEN=not-in-bundle\nservice ready\n")
        if command[:3] == ["docker", "compose", "ps"]:
            return CommandResult(stdout='[{"Service":"api","State":"running"}]')
        return CommandResult()


@pytest.mark.parametrize("ota_error", ["ota_stage_failed", "ota_unsafe_current"])
def test_diagnostic_bundle_contains_only_sanitized_bounded_operational_artifacts(
    host_paths, tmp_path, ota_error
):
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text("SECRET_KEY=outside\n")
    (host_paths.var / "data").mkdir(parents=True)
    (host_paths.var / "data" / "robopark.db").write_text("database")
    (host_paths.var / "data" / "report-attachments").mkdir()
    (host_paths.var / "data" / "report-attachments" / "payload.txt").write_text("user payload")
    host_paths.state.mkdir(parents=True)
    (host_paths.state / "updater-journal.json").write_text(
        json.dumps(
            {
                "job_id": "2e010d12-66c7-4b55-b101-0d6601b1e9c2",
                "phase": "failed",
                "error": "build_failed",
                "candidate": "0.1.8-job",
                "actor_user_id": 1,
                "secret": "not-in-bundle",
            }
        )
    )
    (host_paths.state / "ota-update-journal.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "request": {
                    "operation_id": "11111111-1111-4111-8111-111111111111",
                    "upload_id": "22222222-2222-4222-8222-222222222222",
                    "sha256": "a" * 64,
                    "version": "0.2.0-rc.16",
                    "secret": "nested-not-in-bundle",
                },
                "phase": "failed",
                "error": ota_error,
                "started_at": "2026-10-01T10:00:00+00:00",
                "updated_at": "2026-10-01T10:01:00+00:00",
                "metadata": {"stderr": "raw-not-in-bundle"},
            }
        )
    )
    public = host_paths.ops / "public"
    public.mkdir(parents=True)
    (public / "rebuild.result").write_text(
        json.dumps({"job_id": "2e010d12-66c7-4b55-b101-0d6601b1e9c2", "ok": False, "error": "build_failed"})
    )
    (public / "host-status.json").write_text(
        json.dumps({"job_id": "2e010d12-66c7-4b55-b101-0d6601b1e9c2", "state": "previous_restored", "error": "build_failed"})
    )
    (public / "ota-status.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "operation_id": "11111111-1111-4111-8111-111111111111",
                "version": "0.2.0-rc.16",
                "sha256": "a" * 64,
                "phase": "failed",
                "error": ota_error,
                "stderr": "status-not-in-bundle",
            }
        )
    )
    report = DiagnosticReport([CheckResult("local_endpoint", "ok", "Локальный endpoint доступен", None)])

    bundle = create_diagnostic_bundle(host_paths, report, BundleRunner(), tmp_path / "diagnostics.zip")

    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        data = {name: archive.read(name).decode("utf-8") for name in names}

    assert {"report.json", "compose-services.json", "release-metadata.json", "update-status.json"} <= set(names)
    update = json.loads(data["update-status.json"])
    assert update["journal"] == {
        "candidate": "0.1.8-job",
        "error": "build_failed",
        "job_id": "2e010d12-66c7-4b55-b101-0d6601b1e9c2",
        "phase": "failed",
    }
    assert update["result"]["error"] == "build_failed"
    assert update["status"]["state"] == "previous_restored"
    assert update["ota_journal"] == {
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "version": "0.2.0-rc.16",
        "phase": "failed",
        "error": ota_error,
    }
    assert update["ota_status"] == update["ota_journal"]
    assert any(name.startswith("journal/") for name in names)
    combined = json.dumps(data, ensure_ascii=False)
    assert "not-in-bundle" not in combined
    assert "outside" not in combined
    assert "database" not in combined
    assert "user payload" not in combined
    assert not any(name.endswith((".env", ".db")) for name in names)


@pytest.mark.parametrize("journal", [False, True])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("error", "database_password=not-in-bundle"),
        ("error", []),
        ("error", {}),
        ("phase", []),
        ("phase", {}),
        ("schema", True),
    ],
)
def test_diagnostic_bundle_skips_malformed_ota_state(host_paths, tmp_path, journal, field, value):
    state = {
        "schema": 1,
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "version": "0.2.0-rc.16",
        "phase": "failed",
        "error": "ota_stage_failed",
    }
    state[field] = value
    if journal:
        state["request"] = {
            "operation_id": state.pop("operation_id"),
            "version": state.pop("version"),
        }
        path = host_paths.state / "ota-update-journal.json"
    else:
        path = host_paths.ops / "public/ota-status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state))
    report = DiagnosticReport([])

    bundle = create_diagnostic_bundle(
        host_paths, report, BundleRunner(), tmp_path / "diagnostics.zip"
    )

    with zipfile.ZipFile(bundle) as archive:
        data = archive.read("update-status.json").decode("utf-8")
    assert json.loads(data)["ota_journal" if journal else "ota_status"] == {}
    assert "not-in-bundle" not in data


def test_diagnostics_exports_only_terminal_lifecycle_metadata(host_paths,tmp_path):
    from uuid import uuid4
    sid=str(uuid4());folder=host_paths.state/'terminal';folder.mkdir(parents=True)
    (folder/f'{sid}.json').write_text(json.dumps({'request':{'auth_session_hash':'SECRET_HASH'},
        'session':{'id':sid,'profile':'root','state':'ended','termination_reason':'closed',
                   'input_bytes':12,'output_bytes':99,'input':'SECRET_COMMAND','output':'SECRET_OUTPUT'}}))
    report=DiagnosticReport(checks=(CheckResult('fixture','ok','ok'),))
    bundle=create_diagnostic_bundle(host_paths,report,BundleRunner(),tmp_path/'terminal.zip')
    with zipfile.ZipFile(bundle) as archive:
        text=archive.read('terminal-lifecycle.json').decode()
    assert sid in text and 'closed' in text
    assert 'SECRET_' not in text and 'auth_session_hash' not in text
