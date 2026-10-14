from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts/khadas_test_setup.py"
SPEC = importlib.util.spec_from_file_location("khadas_test_setup", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
setup = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = setup
SPEC.loader.exec_module(setup)


class FakeRunner:
    def __init__(self, *, worker_health: str = "healthy") -> None:
        self.calls: list[list[str]] = []
        self.options: list[dict[str, object]] = []
        self.project_containers = ["a" * 64, "b" * 64]
        self.worker_health = worker_health
        self.enabled_units: set[str] = set()
        self.active_units: set[str] = set()

    def __call__(self, argv, **kwargs):
        call = [os.fspath(value) for value in argv]
        self.calls.append(call)
        self.options.append(kwargs)
        if call[:2] == ["docker", "ps"]:
            output = "\n".join(self.project_containers)
            return subprocess.CompletedProcess(argv, 0, output + ("\n" if output else ""), "")
        if call[:2] == ["docker", "stop"]:
            self.project_containers = []
        if call[:3] == ["docker", "compose", "--project-name"] and call[-3:] == [
            "ps",
            "-q",
            "worker",
        ]:
            return subprocess.CompletedProcess(argv, 0, "c" * 64 + "\n", "")
        if call[:3] == ["docker", "inspect", "--format"]:
            return subprocess.CompletedProcess(argv, 0, self.worker_health + "\n", "")
        if call[:3] == ["systemctl", "enable", "--now"]:
            self.enabled_units.update(call[3:])
            self.active_units.update(call[3:])
        if call[:2] == ["systemctl", "enable"] and call[2:3] != ["--now"]:
            self.enabled_units.update(call[2:])
        if call[:3] == ["systemctl", "disable", "--now"]:
            self.enabled_units.difference_update(call[3:])
            self.active_units.difference_update(call[3:])
        if call[:2] == ["systemctl", "stop"]:
            self.active_units.difference_update(call[2:])
        if call[:3] == ["systemctl", "is-active", "--quiet"]:
            active = call[3] in self.active_units
            return subprocess.CompletedProcess(argv, 0 if active else 3, "", "")
        if call[:3] == ["systemctl", "is-enabled", "--quiet"]:
            enabled = call[3] in self.enabled_units
            return subprocess.CompletedProcess(argv, 0 if enabled else 1, "", "")
        if call == ["pgrep", "-x", "tuna"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _target(profile_sha256: str) -> dict[str, object]:
    return {
        "version": setup.EXPECTED_VERSION,
        "ota_name": setup.EXPECTED_OTA_NAME,
        "ota_sha256": setup.EXPECTED_OTA_SHA256,
        "ota_size": setup.EXPECTED_OTA_SIZE,
        "origin": setup.TEST_ORIGIN,
        "subdomain": "roboparktest",
        "region": "ru",
        "instance_id": "22222222-2222-4222-8222-222222222222",
        "profile_sha256": profile_sha256,
    }


def _bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    monkeypatch.setattr(
        setup, "PINNED_UPDATE_ROOT_B64", base64.b64encode(public).decode()
    )
    profile_payload = json.dumps(
        {
            "purpose": "robopark-deployment-v1",
            "origin": setup.TEST_ORIGIN,
            "instance_id": "22222222-2222-4222-8222-222222222222",
            "environment": "test",
            "channel": "rc",
            "expires": 4102444800,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    profile = json.dumps(
        {
            "payload": base64.b64encode(profile_payload).decode(),
            "signature": base64.b64encode(private.sign(profile_payload)).decode(),
        },
        sort_keys=True,
    ).encode()
    target = _target(hashlib.sha256(profile).hexdigest())
    bundle = tmp_path / "khadas-test-setup.pyz"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("target.json", json.dumps(target, sort_keys=True))
        archive.writestr("profile.json", profile)
        archive.writestr(
            "update-root.json",
            json.dumps({"ed25519": base64.b64encode(public).decode()}),
        )
        # The production constant is deliberately large.  Sparse ZIP metadata cannot
        # represent it, so bundle integrity is covered separately with a small fixture.
    return bundle


def _installed_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    state = root / "var/lib/robopark/ops/state"
    state.mkdir(parents=True)
    compose = state / "compose/old-production.json"
    compose.parent.mkdir()
    compose.write_text('{"services":{"db":{},"api":{},"web":{},"worker":{}}}')
    (state / "current-compose.json").symlink_to("compose/old-production.json")
    release = root / "opt/robopark/releases/0.2.0-rc.43-old"
    release.mkdir(parents=True)
    (release / "VERSION").write_text("0.2.0-rc.43\n")
    (root / "opt/robopark/current").symlink_to("releases/0.2.0-rc.43-old")
    etc = root / "etc/robopark"
    etc.mkdir(parents=True)
    (etc / "host.env").write_text("SECRET_KEY=keep\nROBOPARK_ENVIRONMENT=production\n")
    (etc / "tuna.env").write_text("TUNA_TOKEN=keep-secret\nTUNA_BIND=127.0.0.1:8080\n")
    (etc / "updater.env").write_text("GITHUB_ENABLED=true\nROBOPARK_UPDATE_CHANNEL=stable\n")
    bot = root / "var/lib/robopark/data/telegram-bot/enabled.json"
    bot.parent.mkdir(parents=True)
    bot.write_text('{"schema":1,"enabled":true}\n')
    model = root / "proc/device-tree/model"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"Khadas VIM4\x00")
    return root


def test_validate_target_rejects_any_production_or_unexpected_ota() -> None:
    target = _target("a" * 64)
    setup.validate_target(target)
    with pytest.raises(setup.SetupError, match="target_origin_invalid"):
        setup.validate_target({**target, "origin": "https://robopark.ru.tuna.am"})
    with pytest.raises(setup.SetupError, match="target_ota_invalid"):
        setup.validate_target({**target, "ota_sha256": "b" * 64})


def test_update_root_is_pinned_to_reviewed_public_key() -> None:
    assert (
        setup.PINNED_UPDATE_ROOT_B64
        == "KgfdWbJw01IJlIG4vFzNcTY7EiwWKSJhQakpzgFH58M="
    )


def test_verify_bundle_checks_profile_signature_and_embedded_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ota = b"reviewed-ota"
    monkeypatch.setattr(setup, "EXPECTED_OTA_SIZE", len(ota))
    monkeypatch.setattr(setup, "EXPECTED_OTA_SHA256", hashlib.sha256(ota).hexdigest())
    bundle = _bundle(tmp_path, monkeypatch)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(setup.EXPECTED_OTA_NAME, ota)

    verified = setup.verify_bundle(bundle)

    assert verified.target["origin"] == setup.TEST_ORIGIN
    assert verified.ota == ota
    assert json.loads(verified.profile)["payload"]


def test_quarantine_is_durable_scoped_and_preserves_first_private_backup(
    tmp_path: Path,
) -> None:
    root = _installed_root(tmp_path)
    runner = FakeRunner()
    transition = setup.Transition(root=root, runner=runner)

    transition.bind_target(_target("a" * 64))
    transition.quarantine()
    original_backup = transition.backup_dir / "host.env"
    first = original_backup.read_bytes()
    (root / "etc/robopark/host.env").write_text("SECRET_KEY=changed\n")
    transition.quarantine()

    assert original_backup.read_bytes() == first
    assert json.loads((transition.backup_dir / "bot-enabled.json").read_text()) == {
        "schema": 1,
        "enabled": True,
    }
    assert stat.S_IMODE(original_backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(transition.state_path.stat().st_mode) == 0o600
    app_dropin = root / "etc/systemd/system/robopark.service.d/90-test-transition.conf"
    assert "ExecStartPost=" in app_dropin.read_text()
    assert "test-api-allow" in app_dropin.read_text()
    assert "NEVER_ENABLE_PROD_AUTOMATION" in (
        root
        / "etc/systemd/system/robopark-support-tuna.service.d/90-test-transition.conf"
    ).read_text()
    assert ["docker", "update", "--restart=no", "a" * 64, "b" * 64] in runner.calls
    assert ["docker", "stop", "a" * 64, "b" * 64] in runner.calls
    project_lists = [call for call in runner.calls if call[:2] == ["docker", "ps"]]
    assert any("-a" in call for call in project_lists)
    assert any("-a" not in call for call in project_lists)
    assert not runner.project_containers


def test_quarantine_refuses_an_existing_non_rc43_installation(tmp_path: Path) -> None:
    root = _installed_root(tmp_path)
    wrong = root / "opt/robopark/releases/0.2.0-rc.42-old"
    wrong.mkdir()
    (root / "opt/robopark/current").unlink()
    (root / "opt/robopark/current").symlink_to("releases/0.2.0-rc.42-old")

    with pytest.raises(setup.SetupError, match="rc43_install_required"):
        transition = setup.Transition(root=root, runner=FakeRunner())
        transition.bind_target(_target("a" * 64))
        transition.quarantine()


def test_resume_refuses_a_different_target_identity(tmp_path: Path) -> None:
    root = _installed_root(tmp_path)
    transition = setup.Transition(root=root, runner=FakeRunner())
    transition.bind_target(_target("a" * 64))

    with pytest.raises(setup.SetupError, match="transition_target_changed"):
        transition.bind_target(
            {**_target("b" * 64), "instance_id": "33333333-3333-4333-8333-333333333333"}
        )


def test_transition_rejects_a_preplanted_symlink_state_directory(tmp_path: Path) -> None:
    root = _installed_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    transition_path = root / "var/lib/robopark-test-transition"
    transition_path.symlink_to(outside, target_is_directory=True)

    with pytest.raises(setup.SetupError, match="unsafe_transition_path"):
        setup.Transition(root=root, runner=FakeRunner()).bind_target(_target("a" * 64))


def test_quarantine_fails_closed_when_an_unmanaged_tuna_process_remains(
    tmp_path: Path,
) -> None:
    class TunaRunner(FakeRunner):
        def __call__(self, argv, **kwargs):
            result = super().__call__(argv, **kwargs)
            if [os.fspath(value) for value in argv] == ["pgrep", "-x", "tuna"]:
                return subprocess.CompletedProcess(argv, 0, "1234\n", "")
            return result

    transition = setup.Transition(root=_installed_root(tmp_path), runner=TunaRunner())
    transition.bind_target(_target("a" * 64))

    with pytest.raises(setup.SetupError, match="remaining_tuna_process"):
        transition.quarantine()

    assert transition.read_state()["phase"] == "prepared"


def test_quarantine_archives_pending_production_command_without_dispatch(
    tmp_path: Path,
) -> None:
    root = _installed_root(tmp_path)
    approved = root / "var/lib/robopark/ops/inbox/approved.json"
    approved.parent.mkdir(parents=True)
    old_request = b'{"kind":"restore","job_id":"old-production-operation"}\n'
    approved.write_bytes(old_request)
    api_job = root / setup._LEGACY_API_JOB_PATH
    api_job.parent.mkdir(parents=True)
    api_job.write_text(
        json.dumps(
            {
                "id": "old-production-operation",
                "kind": "restore",
                "state": "running",
                "phase": "awaiting_host",
                "exempt_token_hash": "",
                "restart_required": True,
                "extra": {"host_updater": True},
            }
        )
    )
    runner = FakeRunner()
    transition = setup.Transition(root=root, runner=runner)
    transition.bind_target(_target("a" * 64))

    transition.quarantine()

    assert not approved.exists()
    archived = transition.backup_dir / "pending-inbox-approved.json"
    assert archived.read_bytes() == old_request
    assert stat.S_IMODE(archived.stat().st_mode) == 0o600
    terminal_job = json.loads(api_job.read_text())
    assert terminal_job["state"] == "failed"
    assert terminal_job["error"] == "request_superseded"
    claim = json.loads(
        (root / "var/lib/robopark/ops/public/command-claim.json").read_text()
    )
    assert claim == {
        "job_id": "old-production-operation",
        "kind": "restore",
        "actor_user_id": 0,
        "active": False,
    }
    assert (transition.backup_dir / "pending-legacy-api-ops-job.json").is_file()
    assert not any("consume" in call for call in runner.calls)


def test_quarantine_closes_claimed_command_crash_window(tmp_path: Path) -> None:
    root = _installed_root(tmp_path)
    request = {
        "kind": "diagnostics",
        "job_id": "44444444-4444-4444-8444-444444444444",
        "actor_user_id": 17,
    }
    pending = root / "var/lib/robopark/ops/state/command-request.json"
    pending.write_text(json.dumps(request))
    claim_path = root / "var/lib/robopark/ops/public/command-claim.json"
    claim_path.parent.mkdir(parents=True)
    claim_path.write_text(json.dumps({**request, "active": True}))
    api_job = root / "var/lib/robopark/api-ops/job.json"
    api_job.parent.mkdir(parents=True)
    api_job.write_text(
        json.dumps(
            {
                "id": request["job_id"],
                "kind": request["kind"],
                "state": "running",
                "phase": "awaiting_host",
                "exempt_token_hash": "",
                "restart_required": False,
                "extra": {"host_updater": True, "host_request": request},
            }
        )
    )
    transition = setup.Transition(root=root, runner=FakeRunner())
    transition.bind_target(_target("a" * 64))

    transition.quarantine()
    transition.quarantine()

    assert not pending.exists()
    assert json.loads(api_job.read_text())["state"] == "failed"
    assert json.loads(claim_path.read_text()) == {
        "job_id": request["job_id"],
        "kind": "diagnostics",
        "actor_user_id": 17,
        "active": False,
    }
    assert (
        transition.backup_dir / "pending-command-request.json"
    ).read_text() == json.dumps(request)
    assert json.loads(
        (transition.backup_dir / "pending-command-claim.json").read_text()
    )["active"] is True
    assert (transition.backup_dir / "pending-api-ops-job.json").is_file()


def test_real_api_uid_is_allowed_only_for_the_approved_inbox() -> None:
    transition = setup.Transition(root=Path("/"), runner=FakeRunner())
    assert transition._pending_owner_allowed(
        setup._PENDING_OPERATION_PATHS["inbox-approved.json"], 10001
    )
    assert not transition._pending_owner_allowed(
        setup._PENDING_OPERATION_PATHS["command-request.json"], 10001
    )
    assert transition._pending_owner_allowed(setup._API_JOB_PATH, 10001)


def test_pending_ota_journal_blocks_a_new_update_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _installed_root(tmp_path)
    ota = b"reviewed-ota"
    monkeypatch.setattr(setup, "EXPECTED_OTA_SIZE", len(ota))
    monkeypatch.setattr(setup, "EXPECTED_OTA_SHA256", hashlib.sha256(ota).hexdigest())
    bundle = _bundle(tmp_path, monkeypatch)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(setup.EXPECTED_OTA_NAME, ota)
    journal = root / "var/lib/robopark/ops/state/ota-update-journal.json"
    journal.write_text('{"phase":"cutover"}\n')
    runner = FakeRunner()
    transition = setup.Transition(
        root=root, runner=runner, isolation_callback=lambda **_: None
    )

    with pytest.raises(setup.SetupError, match="ota_journal_invalid"):
        transition.apply(setup.verify_bundle(bundle))

    assert not any(call[-1:] == ["update"] for call in runner.calls)
    assert transition.read_state()["phase"] == "configured"


def test_exact_pending_ota_is_recovered_before_starting_a_new_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _installed_root(tmp_path)
    ota = b"reviewed-ota"
    monkeypatch.setattr(setup, "EXPECTED_OTA_SIZE", len(ota))
    monkeypatch.setattr(setup, "EXPECTED_OTA_SHA256", hashlib.sha256(ota).hexdigest())
    bundle = _bundle(tmp_path, monkeypatch)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(setup.EXPECTED_OTA_NAME, ota)
    journal = root / "var/lib/robopark/ops/state/ota-update-journal.json"
    journal.write_text(
        json.dumps(
            {
                "schema": 1,
                "request": {
                    "operation_id": "11111111-1111-4111-8111-111111111111",
                    "upload_id": "33333333-3333-4333-8333-333333333333",
                    "sha256": setup.EXPECTED_OTA_SHA256,
                    "version": setup.EXPECTED_VERSION,
                },
                "phase": "cutover_started",
                "started_at": "2026-10-14T00:00:00+00:00",
                "updated_at": "2026-10-14T00:01:00+00:00",
                "error": None,
            }
        )
    )

    class RecoveryRunner(FakeRunner):
        phase_before_update: str | None = None

        def __call__(self, argv, **kwargs):
            call = [os.fspath(value) for value in argv]
            result = super().__call__(argv, **kwargs)
            if call[:3] == ["python3", "-I", "-c"]:
                document = json.loads(journal.read_text())
                document["phase"] = "rolled_back"
                document["error"] = "ota_interrupted"
                journal.write_text(json.dumps(document))
            if call[0] == "python3" and call[-1:] == ["update"]:
                state = root / "var/lib/robopark-test-transition/state.json"
                self.phase_before_update = json.loads(state.read_text())["phase"]
            return result

    def http_get(url: str) -> dict:
        if url.endswith("/api/agent-devices/info"):
            profile = root / "var/lib/robopark/data/macos-deployment/profile.json"
            return {
                "environment": "test",
                "instance_id": "22222222-2222-4222-8222-222222222222",
                "signed_profile": json.loads(profile.read_text()),
            }
        return {"status": "ready", "checks": {"database": "ok"}}

    runner = RecoveryRunner()
    transition = setup.Transition(
        root=root,
        runner=runner,
        isolation_callback=lambda **_: None,
        http_get=http_get,
        version_probe=lambda: setup.EXPECTED_VERSION,
    )
    transition.apply(setup.verify_bundle(bundle))

    recovery_index = next(
        index for index, call in enumerate(runner.calls)
        if call[:3] == ["python3", "-I", "-c"]
    )
    update_index = next(
        index for index, call in enumerate(runner.calls)
        if call[0] == "python3" and call[-1:] == ["update"]
    )
    assert recovery_index < update_index
    assert runner.phase_before_update == "configured"
    assert transition.read_state()["phase"] == "complete"


def test_stale_terminal_failed_ota_allows_reviewed_update_from_rc43(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _installed_root(tmp_path)
    ota = b"reviewed-ota"
    monkeypatch.setattr(setup, "EXPECTED_OTA_SIZE", len(ota))
    monkeypatch.setattr(setup, "EXPECTED_OTA_SHA256", hashlib.sha256(ota).hexdigest())
    bundle = _bundle(tmp_path, monkeypatch)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(setup.EXPECTED_OTA_NAME, ota)
    journal = root / "var/lib/robopark/ops/state/ota-update-journal.json"
    journal.write_text(
        json.dumps(
            {
                "schema": 1,
                "request": {
                    "operation_id": "11111111-1111-4111-8111-111111111111",
                    "upload_id": "33333333-3333-4333-8333-333333333333",
                    "sha256": "4" * 64,
                    "version": "0.2.0-rc.44.dev7114536505405942334",
                },
                "phase": "failed",
                "started_at": "2026-10-14T00:00:00+00:00",
                "updated_at": "2026-10-14T00:01:00+00:00",
                "error": "ota_stage_failed",
            }
        )
    )
    runner = FakeRunner()
    transition = setup.Transition(
        root=root, runner=runner, isolation_callback=lambda **_: None
    )

    with pytest.raises(setup.SetupError, match="release_version_unverified"):
        transition.apply(setup.verify_bundle(bundle))

    assert any(call[0] == "python3" and call[-1:] == ["update"] for call in runner.calls)


def test_apply_orders_isolation_before_env_and_ota_then_enables_test_tuna(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _installed_root(tmp_path)
    ota = b"reviewed-ota"
    monkeypatch.setattr(setup, "EXPECTED_OTA_SIZE", len(ota))
    monkeypatch.setattr(setup, "EXPECTED_OTA_SHA256", hashlib.sha256(ota).hexdigest())
    bundle = _bundle(tmp_path, monkeypatch)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(setup.EXPECTED_OTA_NAME, ota)
    runner = FakeRunner()
    events: list[str] = []

    def isolate(*, root: Path, runner, backup_dir: Path) -> None:
        assert backup_dir == root / "var/lib/robopark-test-transition"
        assert not (root / "var/lib/robopark-test-transition/test-api-allow").exists()
        assert "ROBOPARK_ENVIRONMENT=production" in (root / "etc/robopark/host.env").read_text()
        events.append("isolated")

    def http_get(url: str) -> dict:
        events.append(url)
        if url.endswith("/api/agent-devices/info"):
            profile = root / "var/lib/robopark/data/macos-deployment/profile.json"
            return {
                "environment": "test",
                "instance_id": "22222222-2222-4222-8222-222222222222",
                "signed_profile": json.loads(profile.read_text()),
            }
        return {"status": "ready", "checks": {"database": "ok"}}

    transition = setup.Transition(
        root=root,
        runner=runner,
        isolation_callback=isolate,
        http_get=http_get,
        version_probe=lambda: setup.EXPECTED_VERSION,
    )
    transition.apply(setup.verify_bundle(bundle))

    host = (root / "etc/robopark/host.env").read_text()
    tuna = (root / "etc/robopark/tuna.env").read_text()
    updater = (root / "etc/robopark/updater.env").read_text()
    assert events[0] == "isolated"
    assert "ROBOPARK_ENVIRONMENT=test" in host
    assert "ROBOPARK_INSTANCE_ID=22222222-2222-4222-8222-222222222222" in host
    assert f"CORS_ORIGINS={setup.TEST_ORIGIN}" in host
    assert "SESSION_COOKIE_NAME=robopark_test_session" in host
    assert "EMERGENCY_BRIDGE_ENABLED=true" in host
    assert "EMERGENCY_DIRECT_FALLBACK_ENABLED=true" in host
    assert "MAC_CORPORATE_GATEWAY_ENABLED=true" in host
    assert "MAC_CORPORATE_SERVER_FALLBACK_ENABLED=true" in host
    assert "TUNA_TOKEN=keep-secret" in tuna
    assert "TUNA_SUBDOMAIN=roboparktest" in tuna
    assert "TUNA_DOMAIN=" in tuna and "TUNA_LOCATION=ru" in tuna
    assert "TUNA_BIND=127.0.0.1:8080" in tuna
    assert "GITHUB_ENABLED=false" in updater
    assert "ROBOPARK_UPDATE_CHANNEL=manual" in updater
    assert "ROBOPARK_UPDATE_CHANNEL=" not in host
    assert json.loads(
        (root / "var/lib/robopark/data/telegram-bot/enabled.json").read_text()
    ) == {"schema": 1, "enabled": False}
    ota_call = next(call for call in runner.calls if len(call) >= 3 and call[-1] == "update")
    assert ota_call[0] == "python3"
    ota_options = runner.options[runner.calls.index(ota_call)]
    assert ota_options["input"] == "ОБНОВИТЬ\n"
    assert "stdin" not in ota_options
    profile = root / "var/lib/robopark/data/macos-deployment/profile.json"
    assert profile.is_file() and stat.S_IMODE(profile.stat().st_mode) == 0o644
    tuna_start = ["systemctl", "enable", "--now", "robopark-tuna.service"]
    assert tuna_start in runner.calls
    worker_call = next(
        call for call in runner.calls
        if call[-1:] == ["worker"] and "up" in call
    )
    assert "--wait" in worker_call and worker_call[-2] == "180"
    assert runner.calls.index(worker_call) > runner.calls.index(tuna_start)
    assert any(call[:3] == ["docker", "inspect", "--format"] for call in runner.calls)
    automation_start = [
        "systemctl",
        "enable",
        "--now",
        "robopark.service",
        "robopark-commands.path",
        "robopark-doctor.timer",
        "robopark-watchdog.timer",
        "robopark-backup.timer",
    ]
    assert automation_start in runner.calls
    assert ["systemctl", "enable", "robopark-updater.service"] in runner.calls
    assert runner.calls.index(automation_start) > runner.calls.index(worker_call)
    assert not any(
        call[:3] == ["systemctl", "enable", "--now"]
        and "robopark-updater.service" in call
        for call in runner.calls
    )
    for unit in (
        "robopark-commands.service",
        "robopark-commands.path",
        "robopark-updater.service",
        "robopark-doctor.timer",
        "robopark-watchdog.timer",
        "robopark-backup.timer",
    ):
        assert not (
            root / f"etc/systemd/system/{unit}.d/90-test-transition.conf"
        ).exists()
    assert (
        root
        / "etc/systemd/system/robopark-support-tuna.service.d/90-test-transition.conf"
    ).exists()
    assert events[-1] == setup.TEST_ORIGIN + "/api/agent-devices/info"
    assert transition.read_state()["phase"] == "complete"


def test_noninteractive_first_run_quarantines_and_requires_a_second_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _installed_root(tmp_path)
    ota = b"reviewed-ota"
    monkeypatch.setattr(setup, "EXPECTED_OTA_SIZE", len(ota))
    monkeypatch.setattr(setup, "EXPECTED_OTA_SHA256", hashlib.sha256(ota).hexdigest())
    bundle = _bundle(tmp_path, monkeypatch)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(setup.EXPECTED_OTA_NAME, ota)

    result = setup.main(
        ["--non-interactive"],
        root=root,
        runner=FakeRunner(),
        bundle=bundle,
        effective_uid=0,
        machine="aarch64",
        isolation_callback=lambda **_: None,
    )

    assert result == setup.RESUME_REQUIRED
    assert setup.Transition(root=root, runner=FakeRunner()).read_state()["phase"] == "quarantined"
    assert not (root / "var/lib/robopark-test-transition/test-api-allow").exists()


def test_apply_refuses_a_non_khadas_aarch64_host_before_writing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _installed_root(tmp_path)
    (root / "proc/device-tree/model").write_bytes(b"NVIDIA Jetson AGX Orin\x00")
    ota = b"reviewed-ota"
    monkeypatch.setattr(setup, "EXPECTED_OTA_SIZE", len(ota))
    monkeypatch.setattr(setup, "EXPECTED_OTA_SHA256", hashlib.sha256(ota).hexdigest())
    bundle = _bundle(tmp_path, monkeypatch)
    with zipfile.ZipFile(bundle, "a") as archive:
        archive.writestr(setup.EXPECTED_OTA_NAME, ota)

    result = setup.main(
        ["--non-interactive"],
        root=root,
        runner=FakeRunner(),
        bundle=bundle,
        effective_uid=0,
        machine="aarch64",
        isolation_callback=lambda **_: None,
    )

    assert result == 1
    assert not (root / "var/lib/robopark-test-transition/state.json").exists()


def test_isolation_failure_stays_quarantined_and_never_enables_api(tmp_path: Path) -> None:
    root = _installed_root(tmp_path)
    transition = setup.Transition(
        root=root,
        runner=FakeRunner(),
        isolation_callback=lambda **_: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )
    transition.bind_target(_target("a" * 64))
    transition.quarantine()
    verified = setup.VerifiedBundle(
        target=_target("a" * 64), ota=b"unused", profile=b"unused"
    )

    with pytest.raises(setup.SetupError, match="isolation_failed"):
        transition.apply(verified)

    state = transition.read_state()
    assert state["phase"] == "quarantined"
    assert state["status"] == "failed"
    assert state["error"] == "isolation_failed"
    assert state["target"]["instance_id"] == "22222222-2222-4222-8222-222222222222"
    assert not transition.api_allow.exists()


def test_unhealthy_worker_cannot_restore_automation_or_complete(tmp_path: Path) -> None:
    root = _installed_root(tmp_path)
    transition = setup.Transition(root=root, runner=FakeRunner(worker_health="starting"))
    transition.bind_target(_target("a" * 64))
    transition.quarantine()

    with pytest.raises(setup.SetupError, match="worker_health_unverified"):
        transition._enable_worker()

    assert (
        root
        / "etc/systemd/system/robopark-commands.path.d/90-test-transition.conf"
    ).exists()
    assert transition.read_state()["phase"] == "quarantined"


def test_complete_resume_rechecks_worker_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _installed_root(tmp_path)
    transition = setup.Transition(root=root, runner=FakeRunner(worker_health="unhealthy"))
    target = _target("a" * 64)
    transition.bind_target(target)
    transition._state("complete", status="complete")
    monkeypatch.setattr(transition, "_verify_final", lambda _: None)
    monkeypatch.setattr(transition, "_verify_safe_automation_restored", lambda: None)

    with pytest.raises(setup.SetupError, match="worker_health_unverified"):
        transition.apply(setup.VerifiedBundle(target=target, ota=b"unused", profile=b"unused"))


def test_public_tuna_startup_retries_transport_but_not_wrong_identity(tmp_path: Path) -> None:
    profile = json.dumps({"signed": "profile"}).encode()
    verified = setup.VerifiedBundle(target=_target("a" * 64), ota=b"", profile=profile)
    sleeps: list[float] = []
    attempts = 0

    def starting_http(_: str) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise setup.SetupError("health_check_failed")
        return {
            "environment": "test",
            "instance_id": verified.target["instance_id"],
            "signed_profile": json.loads(profile),
        }

    setup.Transition(
        root=tmp_path,
        runner=FakeRunner(),
        http_get=starting_http,
        sleep_fn=sleeps.append,
    )._wait_public(verified)
    assert attempts == 3
    assert sleeps == [5, 5]

    sleeps.clear()
    wrong = setup.Transition(
        root=tmp_path,
        runner=FakeRunner(),
        http_get=lambda _: {
            "environment": "production",
            "instance_id": verified.target["instance_id"],
            "signed_profile": json.loads(profile),
        },
        sleep_fn=sleeps.append,
    )
    with pytest.raises(setup.SetupError, match="test_origin_identity_unverified"):
        wrong._wait_public(verified)
    assert sleeps == []
