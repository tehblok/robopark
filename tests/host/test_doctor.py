import json
import os
import threading

from robopark_host.checks import CommandResult
from robopark_host.doctor import run_doctor, run_status
from robopark_host.watchdog import run_watchdog


class DoctorRunner:
    def __init__(self, db_ready=True):
        self.db_ready = db_ready
        self.calls = []

    def __call__(self, command, *, timeout, max_output):
        self.calls.append((command, timeout, max_output))
        if command == ["docker", "compose", "ps", "--format", "json"]:
            return CommandResult(stdout='[{"Service":"api","State":"running","Health":"healthy"}]')
        if command == ["docker", "compose", "exec", "-T", "api", "python", "-c", "from pathlib import Path; assert Path('/data/robopark.db').exists()"]:
            return CommandResult(returncode=0 if self.db_ready else 1)
        return CommandResult()


class ReadyHttp:
    def __init__(self, db_ready=True):
        self.db_ready = db_ready

    def get(self, url, *, timeout):
        return type(
            "Response",
            (),
            {
                "status": 200,
                "headers": {"X-Content-Type-Options": "nosniff"},
                "json": lambda response: {
                    "status": "ready" if self.db_ready else "degraded",
                    "checks": {
                        "database": "ok" if self.db_ready else "error",
                        "integrations": "ok",
                    },
                },
            },
        )()


class UnreadyHttp:
    def get(self, url, *, timeout):
        raise OSError("connection refused")


def test_database_failure_is_reported_but_never_auto_repaired(host_paths):
    report = run_doctor(host_paths, DoctorRunner(), ReadyHttp(db_ready=False))

    item = report.by_code("database_unavailable")

    assert item.status == "failed"
    assert item.repair is None


def test_doctor_uses_bounded_argument_array_commands_and_never_exposes_env_values(host_paths):
    host_paths.etc.mkdir(parents=True)
    host_env = host_paths.etc / "host.env"
    host_env.write_text("SECRET_KEY=do-not-show\nTUNA_TOKEN=also-secret\n")
    os.chmod(host_env, 0o600)
    runner = DoctorRunner()

    report = run_doctor(host_paths, runner, ReadyHttp())

    assert all(isinstance(command, list) for command, _, _ in runner.calls)
    assert all(timeout > 0 and max_output <= 16_384 for _, timeout, max_output in runner.calls)
    serialized = json.dumps(report.as_dict(), ensure_ascii=False)
    assert "do-not-show" not in serialized
    assert "also-secret" not in serialized
    assert report.by_code("configuration").status in {"ok", "warning"}


def test_doctor_persists_a_bounded_secret_free_json_report_and_human_log(host_paths):
    run_doctor(host_paths, DoctorRunner(), ReadyHttp())

    report = host_paths.var / "diagnostics" / "latest.json"
    log = host_paths.root / "var/log/robopark/doctor.log"

    assert json.loads(report.read_text())["checks"]
    assert "Локальный веб-интерфейс" in log.read_text()
    assert len(log.read_bytes()) <= 16_384


def test_doctor_detects_cross_file_public_url_mismatch_without_exposing_either_value(host_paths):
    host_paths.etc.mkdir(parents=True)
    host_env = host_paths.etc / "host.env"
    tuna_env = host_paths.etc / "tuna.env"
    host_env.write_text("SECRET_KEY=hidden\nPUBLIC_SITE_ORIGIN=https://one.example\n")
    tuna_env.write_text("TUNA_TOKEN=hidden\nPUBLIC_URL=https://two.example\n")
    os.chmod(host_env, 0o600)
    os.chmod(tuna_env, 0o600)

    report = run_doctor(host_paths, DoctorRunner(), ReadyHttp())

    assert report.by_code("configuration").status == "failed"
    assert "one.example" not in json.dumps(report.as_dict())
    assert "two.example" not in json.dumps(report.as_dict())


def test_status_is_a_concise_secret_free_view_of_diagnostics(host_paths):
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "tuna.env").write_text("TUNA_TOKEN=hidden\n")

    status = run_status(host_paths, DoctorRunner(), ReadyHttp())

    assert set(status) >= {"services", "resources", "version", "url", "last_backup"}
    assert "hidden" not in json.dumps(status)


def test_doctor_publishes_sanitized_services_without_losing_other_public_sections(host_paths):
    from robopark_host.state import atomic_write_json

    public = host_paths.var / "api-ops/host-health.json"
    atomic_write_json(public, {
        "capabilities": {"profile": "orin"},
        "storage": {"pressure": False},
    })
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "tuna.env").write_text(
        "TUNA_TOKEN=private-token\nPUBLIC_URL=https://secret.example/path\n"
    )

    class ServiceRunner(DoctorRunner):
        def __call__(self, command, *, timeout, max_output):
            if command[-3:] == ["ps", "--format", "json"]:
                self.calls.append((command, timeout, max_output))
                rows = [
                    {"Service": service, "State": "running", "Health": "healthy"}
                    for service in ("db", "api", "web")
                ]
                return CommandResult(stdout=json.dumps(rows))
            return super().__call__(command, timeout=timeout, max_output=max_output)

    runner = ServiceRunner()
    run_doctor(host_paths, runner, ReadyHttp())
    value = json.loads(public.read_text())
    assert value["capabilities"]["profile"] == "generic-arm"
    assert value["storage"] == {"pressure": False}
    assert value["services"] == {
        "docker": "ok", "tuna": "ok", "internet": "ok", "wifi": "unknown"
    }
    assert isinstance(value["services_checked_at"], str)
    assert len(public.read_bytes()) < 65_536
    assert "private-token" not in public.read_text()
    assert "secret.example" not in public.read_text()
    assert all(timeout <= 10 and max_output <= 16_384 for _, timeout, max_output in runner.calls)


def test_doctor_marks_failed_bounded_internet_probe_degraded(host_paths):
    class OfflineRunner(DoctorRunner):
        def __call__(self, command, *, timeout, max_output):
            if command and command[0] == "curl":
                self.calls.append((command, timeout, max_output))
                return CommandResult(returncode=124, stderr="https://private.example/token")
            return super().__call__(command, timeout=timeout, max_output=max_output)

    runner = OfflineRunner()
    run_doctor(host_paths, runner, ReadyHttp())
    value = json.loads((host_paths.var / "api-ops/host-health.json").read_text())
    assert value["services"]["internet"] == "degraded"
    assert "private.example" not in json.dumps(value)
    assert "token" not in json.dumps(value)
    assert any(command[0] == "curl" and timeout <= 10 for command, timeout, _ in runner.calls)


def test_host_service_projection_marks_missing_probe_unknown(host_paths):
    from robopark_host.checks import CheckResult, DiagnosticReport
    from robopark_host.doctor import _publish_service_health

    _publish_service_health(
        host_paths,
        DiagnosticReport([CheckResult("dns", "ok", "DNS reachable")]),
    )
    value = json.loads((host_paths.var / "api-ops/host-health.json").read_text())
    assert value["services"] == {
        "docker": "unknown", "tuna": "unknown", "internet": "unknown", "wifi": "unknown"
    }


def test_wifi_service_reports_link_state_without_network_identifiers(tmp_path):
    from robopark_host.doctor import _wifi_service_state

    wireless = tmp_path / "wireless"
    net = tmp_path / "net"
    (net / "wlan0").mkdir(parents=True)
    (net / "wlan0" / "operstate").write_text("up\n")
    wireless.write_text("Inter-| sta\n face | data\n wlan0: 0000 70.  -40.  -256\n")

    assert _wifi_service_state(wireless, net) == "ok"
    (net / "wlan0" / "operstate").write_text("down\n")
    assert _wifi_service_state(wireless, net) == "degraded"
    wireless.write_text("Inter-| sta\n face | data\n")
    assert _wifi_service_state(wireless, net) == "unknown"
    (net / "wlan0" / "phy80211").mkdir()
    assert _wifi_service_state(wireless, net) == "degraded"
    wireless.unlink()
    (net / "wlan0" / "operstate").write_text("up\n")
    assert _wifi_service_state(wireless, net) == "ok"


def test_doctor_publishes_wifi_state_as_bounded_service_enum(host_paths, monkeypatch):
    from robopark_host import doctor

    monkeypatch.setattr(doctor, "_wifi_service_state", lambda: "degraded")
    doctor.run_doctor(host_paths, DoctorRunner(), ReadyHttp())
    public = json.loads((host_paths.var / "api-ops/host-health.json").read_text())

    assert public["services"]["wifi"] == "degraded"
    assert "wlan0" not in json.dumps(public)


def test_public_health_section_updates_do_not_lose_concurrent_writer(host_paths, monkeypatch):
    from robopark_host import health_projection

    public = host_paths.var / "api-ops/host-health.json"
    first_read = threading.Event()
    release_first = threading.Event()
    original_read = health_projection.read_object

    def delayed_first_read(path):
        value = original_read(path)
        if threading.current_thread().name == "capabilities-writer":
            first_read.set()
            assert release_first.wait(2)
        return value

    monkeypatch.setattr(health_projection, "read_object", delayed_first_read)
    first = threading.Thread(
        name="capabilities-writer",
        target=lambda: health_projection.update_public_health(
            public, capabilities={"profile": "orin"}
        ),
    )
    second = threading.Thread(
        name="storage-writer",
        target=lambda: health_projection.update_public_health(
            public, storage={"pressure": False}
        ),
    )
    first.start()
    assert first_read.wait(2)
    second.start()
    second.join(timeout=0.1)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive() and not second.is_alive()
    assert json.loads(public.read_text()) == {
        "capabilities": {"profile": "orin"}, "storage": {"pressure": False}
    }


def test_watchdog_restarts_the_application_only_after_three_local_readiness_failures(host_paths):
    runner = DoctorRunner()
    http = UnreadyHttp()

    assert run_watchdog(host_paths, runner, http).restarted is None
    assert run_watchdog(host_paths, runner, http).restarted is None
    result = run_watchdog(host_paths, runner, http)

    assert result.restarted == "restart_app"
    assert [call[0] for call in runner.calls].count(["systemctl", "restart", "robopark.service"]) == 1


def test_watchdog_resets_consecutive_failures_after_local_readiness_recovers(host_paths):
    runner = DoctorRunner()

    run_watchdog(host_paths, runner, UnreadyHttp())
    run_watchdog(host_paths, runner, UnreadyHttp())
    result = run_watchdog(host_paths, runner, ReadyHttp())

    assert result.consecutive_failures == 0
    assert json.loads((host_paths.state / "watchdog.json").read_text())["consecutive_failures"] == 0
