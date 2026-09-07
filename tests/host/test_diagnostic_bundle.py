import json
import zipfile

from robopark_host.bundle import create_diagnostic_bundle
from robopark_host.checks import CheckResult, CommandResult, DiagnosticReport


class BundleRunner:
    def __call__(self, command, *, timeout, max_output):
        if command[0] == "journalctl":
            return CommandResult(stdout="TUNA_TOKEN=not-in-bundle\nservice ready\n")
        if command[:3] == ["docker", "compose", "ps"]:
            return CommandResult(stdout='[{"Service":"api","State":"running"}]')
        return CommandResult()


def test_diagnostic_bundle_contains_only_sanitized_bounded_operational_artifacts(host_paths, tmp_path):
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
    public = host_paths.ops / "public"
    public.mkdir(parents=True)
    (public / "rebuild.result").write_text(
        json.dumps({"job_id": "2e010d12-66c7-4b55-b101-0d6601b1e9c2", "ok": False, "error": "build_failed"})
    )
    (public / "host-status.json").write_text(
        json.dumps({"job_id": "2e010d12-66c7-4b55-b101-0d6601b1e9c2", "state": "previous_restored", "error": "build_failed"})
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
    assert any(name.startswith("journal/") for name in names)
    combined = json.dumps(data, ensure_ascii=False)
    assert "not-in-bundle" not in combined
    assert "outside" not in combined
    assert "database" not in combined
    assert "user payload" not in combined
    assert not any(name.endswith(".env") or name.endswith(".db") for name in names)
