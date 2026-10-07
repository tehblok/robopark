from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from robopark_host.ota_store import OtaPackageStore
from robopark_host.ota_update import (
    OtaProductionEffects,
    OtaUpdateEngine,
    OtaUpdateRequest,
    SystemOtaUpdateRuntime,
)
from robopark_host.state import atomic_write_json
from robopark_ota import OtaError
from test_ota_store import make_ota


@dataclass
class FakeRuntime:
    fail_at: str | None = None
    calls: list[str] = field(default_factory=list)

    def _call(self, name):
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(name)

    def current_version(self):
        return "0.2.0-rc.6"

    def snapshot(self, request, package):
        self._call("snapshot")

    def abort_snapshot(self, request, package):
        self._call("abort_snapshot")

    def stage(self, request, package):
        self._call("stage")

    def migrate(self, request, package):
        self._call("migrate")

    def cutover(self, request, package):
        self._call("cutover")

    def health_check(self, request, package):
        self._call("health_check")

    def publish(self, request, package):
        self._call("publish")

    def resume(self, request):
        self._call("resume")

    def rollback(self, request, package):
        self._call("rollback")

    def cleanup(self, request, package):
        self._call("cleanup")


def request_for(host_paths, *, version="0.2.0-rc.7"):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload, version=version)
    return OtaUpdateRequest(
        operation_id=uuid4(),
        upload_id=upload_id,
        sha256=digest,
        version=version,
    )


def typed_command_for(host_paths, request, adapter):
    from robopark_host.operation_capabilities import current_capability_revision

    boot = host_paths.root / "proc/sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True, exist_ok=True)
    if not boot.exists():
        boot.write_text(str(uuid4()) + "\n")
    created_at = datetime.now(UTC).isoformat()
    return {
        "job_id": str(request.operation_id), "kind": "ota-update", "actor_user_id": 1,
        "created_at": created_at,
        "capability_revision": current_capability_revision(host_paths, adapter),
        "confirmation": "UPDATE ROBOPARK", "upload_id": str(request.upload_id),
        "sha256": request.sha256, "version": request.version,
        "authorization": {
            "operation_id": str(request.operation_id), "operation_kind": "ota-update",
            "actor_user_id": 1, "consumed": True, "validated_at": created_at,
        },
    }


def test_update_runs_safe_phases_and_same_operation_is_idempotent(host_paths):
    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    request = request_for(host_paths)

    first = engine.apply(request)
    second = engine.apply(request)

    assert first == second
    assert first.phase == "published"
    assert first.error is None
    assert first.sha256 == request.sha256
    assert runtime.calls == [
        "stage", "snapshot", "migrate", "cutover", "health_check", "publish", "resume",
        "cleanup", "cleanup",
    ]


def test_stage_failure_preserves_live_writes_without_taking_snapshot(host_paths):
    @dataclass
    class LiveWriteRuntime(FakeRuntime):
        live_value: str = "before-snapshot"
        snapshot_value: str | None = None

        def snapshot(self, request, package):
            self.snapshot_value = self.live_value
            self._call("snapshot")

        def stage(self, request, package):
            self.live_value = "written-while-candidate-builds"
            self._call("stage")

        def rollback(self, request, package):
            self.live_value = self.snapshot_value
            self._call("rollback")

    runtime = LiveWriteRuntime(fail_at="stage")

    receipt = OtaUpdateEngine(host_paths, runtime).apply(request_for(host_paths))

    assert receipt.phase == "failed"
    assert receipt.error == "ota_stage_failed"
    assert runtime.live_value == "written-while-candidate-builds"
    assert runtime.calls == ["stage", "cleanup"]


def test_final_snapshot_captures_writes_accepted_during_candidate_build(host_paths):
    @dataclass
    class LiveWriteRuntime(FakeRuntime):
        live_value: str = "before-build"
        snapshot_value: str | None = None

        def stage(self, request, package):
            self.live_value = "accepted-during-build"
            self._call("stage")

        def snapshot(self, request, package):
            self.snapshot_value = self.live_value
            self._call("snapshot")

        def migrate(self, request, package):
            self.live_value = "partially-migrated"
            self._call("migrate")

        def rollback(self, request, package):
            self.live_value = self.snapshot_value
            self._call("rollback")

    runtime = LiveWriteRuntime(fail_at="migrate")

    receipt = OtaUpdateEngine(host_paths, runtime).apply(request_for(host_paths))

    assert receipt.phase == "rolled_back"
    assert runtime.live_value == "accepted-during-build"
    assert runtime.calls[:3] == ["stage", "snapshot", "migrate"]


def test_published_journal_is_durable_before_writes_resume(monkeypatch, host_paths):
    request = request_for(host_paths)
    engine = OtaUpdateEngine(host_paths, FakeRuntime())
    interrupted = False

    class PowerLoss(BaseException):
        pass

    def resume_after_terminal(_request):
        nonlocal interrupted
        journal = json.loads(engine.journal_path.read_text())
        assert journal["phase"] == "published"
        assert not engine._receipt_path(request).exists()
        engine.runtime._call("resume")
        if not interrupted:
            interrupted = True
            raise PowerLoss()

    monkeypatch.setattr(engine.runtime, "resume", resume_after_terminal)

    with pytest.raises(PowerLoss):
        engine.apply(request)

    receipt = engine.apply(request)

    assert receipt.phase == "published"
    assert engine.runtime.calls.count("publish") == 1
    assert engine.runtime.calls.count("resume") == 2


@pytest.mark.parametrize(
    ("phase", "error"),
    [("published", None), ("rolled_back", "ota_health_check_failed")],
)
def test_boot_recovery_resumes_terminal_journal_without_receipt(
    host_paths, phase, error
):
    request = request_for(host_paths)
    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    engine._write_journal(request, phase, error=error)

    receipt = engine.recover()

    assert receipt.phase == phase
    assert receipt.error == error
    assert runtime.calls == ["resume", "cleanup"]
    assert engine._existing_receipt(request) == receipt


def test_boot_recovery_repeats_only_bounded_cleanup_for_terminal_receipt(host_paths):
    request = request_for(host_paths)
    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    receipt = engine.apply(request)
    calls = list(runtime.calls)

    assert engine.recover() is None
    assert engine._existing_receipt(request) == receipt
    assert runtime.calls == calls + ["cleanup"]


@pytest.mark.parametrize("replay", ["recover", "apply"])
def test_boot_recovery_preserves_manual_artifacts_for_failed_rollback(
    host_paths, replay
):
    class DestructiveCleanupRuntime(FakeRuntime):
        def cleanup(self, request, package):
            self._call("cleanup")
            snapshot = host_paths.ops / "rollbacks" / str(request.operation_id)
            candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
            snapshot.rmdir()
            candidate.rmdir()

    request = request_for(host_paths)
    runtime = DestructiveCleanupRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    snapshot = host_paths.ops / "rollbacks" / str(request.operation_id)
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
    snapshot.mkdir(parents=True)
    candidate.mkdir(parents=True)
    engine._write_journal(request, "failed", error="ota_rollback_failed")

    receipt = engine.apply(request) if replay == "apply" else engine.recover()

    assert receipt.error == "ota_rollback_failed"
    assert runtime.calls == []
    assert snapshot.is_dir()
    assert candidate.is_dir()
    assert engine.recover() is None
    assert engine.apply(request) == receipt
    assert runtime.calls == []


def test_terminal_journal_is_durable_before_receipt_so_next_ota_is_not_stranded(
    monkeypatch, host_paths
):
    from robopark_host import ota_update

    first = request_for(host_paths)
    second = request_for(host_paths, version="0.2.0-rc.8")
    runtime = FakeRuntime(fail_at="health_check")
    engine = OtaUpdateEngine(host_paths, runtime)
    receipt_path = engine._receipt_path(first)
    original_write = ota_update.atomic_write_json
    interrupted = False

    class PowerLoss(BaseException):
        pass

    def interrupt_before_receipt(path, value, *args, **kwargs):
        nonlocal interrupted
        if path == receipt_path and not interrupted:
            interrupted = True
            raise PowerLoss()
        return original_write(path, value, *args, **kwargs)

    monkeypatch.setattr(ota_update, "atomic_write_json", interrupt_before_receipt)

    with pytest.raises(PowerLoss):
        engine.apply(first)

    journal = json.loads(engine.journal_path.read_text())
    assert journal["phase"] == "rolled_back"
    assert not receipt_path.exists()
    calls_before_replay = list(runtime.calls)
    recovered = OtaUpdateEngine(host_paths, runtime).apply(first)
    assert recovered.phase == "rolled_back"
    assert runtime.calls == calls_before_replay + ["resume", "cleanup"]
    assert receipt_path.is_file()
    assert OtaUpdateEngine(host_paths, FakeRuntime()).apply(second).phase == "published"


@pytest.mark.parametrize("manifest_contents", [None, "{", '{"app_version": 12}', '{"app_version": "???"}'])
def test_existing_release_with_unknown_version_fails_before_ota_snapshot(host_paths, manifest_contents):
    release = host_paths.releases / "installed"
    release.mkdir(parents=True)
    if manifest_contents is not None:
        (release / "manifest.json").write_text(manifest_contents)
    host_paths.current.symlink_to(release)
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload, compatible=())
    request = OtaUpdateRequest(uuid4(), upload_id, digest, "0.2.0-rc.7")
    runtime = SystemOtaUpdateRuntime(host_paths, object())

    with pytest.raises(OtaError, match="ota_current_version_unknown"):
        runtime.current_version()
    receipt = OtaUpdateEngine(host_paths, runtime).apply(request)

    assert receipt.phase == "failed"
    assert receipt.error == "ota_current_version_unknown"
    assert host_paths.current.resolve() == release
    assert not host_paths.previous.exists()
    assert upload.is_file()
    assert not (host_paths.state / "ota-packages" / f"{digest}.ota").exists()


def test_missing_current_is_a_clean_install(host_paths):
    assert SystemOtaUpdateRuntime(host_paths, object()).current_version() is None


def test_production_effect_reports_rolled_back_ota_as_failure(host_paths):
    from robopark_host.release import ReleaseError

    request = request_for(host_paths)
    effects = OtaProductionEffects(host_paths, object())
    effects.engine = OtaUpdateEngine(host_paths, FakeRuntime(fail_at="health_check"))

    with pytest.raises(ReleaseError, match="ota_update_failed"):
        effects.ota_update(
            str(request.operation_id), str(request.upload_id), request.sha256, request.version
        )

    assert effects.engine._existing_receipt(request).phase == "rolled_back"


def test_manual_rollback_resolves_only_current_ota_metadata(host_paths, monkeypatch):
    update_id = uuid4()
    version = "0.2.0-rc.7"
    candidate = host_paths.releases / f"{version}-{update_id}"
    previous = host_paths.releases / "0.2.0-rc.6-release"
    candidate.mkdir(parents=True)
    previous.mkdir()
    host_paths.current.symlink_to(candidate)
    host_paths.previous.symlink_to(previous)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{update_id}.json",
        {
            "schema": 1,
            "operation_id": str(update_id),
            "candidate": candidate.name,
            "current": previous.name,
            "previous": None,
            "compose": f"compose/{update_id}-previous.json",
            "release_status": None,
        },
    )
    runtime = SystemOtaUpdateRuntime(host_paths, object())
    calls = []
    monkeypatch.setattr(runtime, "rollback", lambda request, package: calls.append(("rollback", request, package)))
    monkeypatch.setattr(runtime, "resume", lambda request: calls.append(("resume", request)))

    assert runtime.manual_rollback(previous.name) == {
        "release": previous.name,
        "rolled_back": True,
    }
    assert calls[0][0] == "rollback"
    assert calls[0][1].operation_id == update_id
    assert calls[1][0] == "resume"


def test_production_effect_delegates_manual_rollback_to_runtime(host_paths):
    effects = OtaProductionEffects(host_paths, object())
    effects.engine.runtime = SimpleNamespace(
        manual_rollback=lambda release: {"release": release, "rolled_back": True}
    )

    assert effects.manual_rollback(str(uuid4()), "0.2.0-rc.6-release") == {
        "release": "0.2.0-rc.6-release",
        "rolled_back": True,
    }


def test_dispatched_ota_reconcile_reads_receipt_without_repeating_runtime(host_paths):
    from robopark_host.commands import OperationKind, SafeProductionTypedHostEffects

    request = request_for(host_paths)
    runtime = FakeRuntime()
    effects = OtaProductionEffects(host_paths, object())
    effects.engine = OtaUpdateEngine(host_paths, runtime)
    assert effects.engine.apply(request).phase == "published"
    original_calls = list(runtime.calls)
    adapter = SafeProductionTypedHostEffects(host_paths, ota_effects=effects)
    operation = SimpleNamespace(
        kind=OperationKind.OTA_UPDATE,
        operation_id=str(request.operation_id),
        payload={
            "upload_id": str(request.upload_id),
            "sha256": request.sha256,
            "version": request.version,
        },
    )

    result = adapter.reconcile(operation)

    assert result["state"] == "succeeded"
    assert result["detail"]["phase"] == "published"
    assert runtime.calls == original_calls + ["cleanup"]


@pytest.mark.parametrize(
    ("fail_at", "expected_state", "expected_phase"),
    [(None, "succeeded", "published"), ("health_check", "failed", "rolled_back")],
)
def test_typed_ota_dispatch_checkpoint_recovers_through_durable_engine(
    host_paths, fail_at, expected_state, expected_phase
):
    from robopark_host.commands import (
        SafeProductionTypedHostEffects,
        execute_typed_operation,
        validate_typed_operation,
    )
    from robopark_host.operation_capabilities import current_capability_revision

    boot = host_paths.root / "proc/sys/kernel/random/boot_id"
    boot.parent.mkdir(parents=True, exist_ok=True)
    boot.write_text(str(uuid4()) + "\n")
    request = request_for(host_paths)
    runtime = FakeRuntime(fail_at=fail_at)
    effects = OtaProductionEffects(host_paths, object())
    effects.engine = OtaUpdateEngine(host_paths, runtime)
    adapter = SafeProductionTypedHostEffects(host_paths, ota_effects=effects)
    created_at = datetime.now(UTC).isoformat()
    command = {
        "job_id": str(request.operation_id),
        "kind": "ota-update",
        "actor_user_id": 1,
        "created_at": created_at,
        "capability_revision": current_capability_revision(host_paths, adapter),
        "confirmation": "UPDATE ROBOPARK",
        "upload_id": str(request.upload_id),
        "sha256": request.sha256,
        "version": request.version,
        "authorization": {
            "operation_id": str(request.operation_id),
            "operation_kind": "ota-update",
            "actor_user_id": 1,
            "consumed": True,
            "validated_at": created_at,
        },
    }
    operation = validate_typed_operation(command)
    atomic_write_json(
        host_paths.state / "typed-operation-dispatch" / f"{request.operation_id}.json",
        {"schema": 1, "request": operation.request, "state": "dispatched"},
    )

    result = execute_typed_operation(host_paths, command, adapter)

    assert result["state"] == expected_state
    assert effects.engine._existing_receipt(request).phase == expected_phase
    calls = list(runtime.calls)
    assert execute_typed_operation(host_paths, command, adapter) == result
    assert runtime.calls == calls


def test_typed_ota_replay_retries_terminal_resume_after_transient_failure(host_paths):
    from robopark_host.commands import (
        SafeProductionTypedHostEffects,
        consume_commands,
        execute_typed_operation,
    )
    from robopark_host.ota_update import OtaFinalizationPending

    boot = host_paths.root / "proc/sys/kernel/random/boot_id"
    request = request_for(host_paths)
    runtime = FakeRuntime(fail_at="resume")
    effects = OtaProductionEffects(host_paths, object())
    effects.engine = OtaUpdateEngine(host_paths, runtime)
    adapter = SafeProductionTypedHostEffects(host_paths, ota_effects=effects)
    command = typed_command_for(host_paths, request, adapter)

    with pytest.raises(OtaFinalizationPending):
        execute_typed_operation(host_paths, command, adapter)
    assert effects.engine._read_journal()["phase"] == "published"
    assert effects.engine._existing_receipt(request) is None
    assert not (host_paths.state / "typed-operation-receipts" / f"{request.operation_id}.json").exists()

    runtime.fail_at = None
    boot.write_text(str(uuid4()) + "\n")  # Recovery may happen after a reboot.
    atomic_write_json(host_paths.state / "command-request.json", command)
    assert consume_commands(host_paths, None, None, typed_effects=adapter) == 0
    typed_receipt = host_paths.state / "typed-operation-receipts" / f"{request.operation_id}.json"
    recovered = json.loads(typed_receipt.read_text())["result"]
    assert recovered["state"] == "succeeded"
    assert not (host_paths.state / "command-request.json").exists()
    assert effects.engine._existing_receipt(request).phase == "published"
    assert runtime.calls.count("publish") == 1
    assert runtime.calls.count("resume") == 2


def test_typed_ota_retries_receipt_write_after_published_resume(host_paths, monkeypatch):
    from robopark_host import ota_update
    from robopark_host.commands import (
        SafeProductionTypedHostEffects,
        execute_typed_operation,
    )
    from robopark_host.ota_update import OtaFinalizationPending

    request = request_for(host_paths)
    runtime = FakeRuntime()
    effects = OtaProductionEffects(host_paths, object())
    effects.engine = OtaUpdateEngine(host_paths, runtime)
    adapter = SafeProductionTypedHostEffects(host_paths, ota_effects=effects)
    command = typed_command_for(host_paths, request, adapter)
    write = ota_update.atomic_write_json

    def fail_receipt(path, value, **kwargs):
        if path.parent == effects.engine.receipts:
            raise OSError("receipt_unavailable")
        return write(path, value, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(ota_update, "atomic_write_json", fail_receipt)
        with pytest.raises(OtaFinalizationPending):
            execute_typed_operation(host_paths, command, adapter)

    assert effects.engine._read_journal()["phase"] == "published"
    assert effects.engine._existing_receipt(request) is None
    assert runtime.calls.count("resume") == 1
    assert not (host_paths.state / "typed-operation-receipts" / f"{request.operation_id}.json").exists()

    recovered = execute_typed_operation(host_paths, command, adapter)
    assert recovered["state"] == "succeeded"
    assert effects.engine._existing_receipt(request).phase == "published"
    assert runtime.calls.count("publish") == 1
    assert runtime.calls.count("resume") == 2


def test_update_rejects_manifest_disk_requirement_before_snapshot(host_paths):
    runtime = FakeRuntime()
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload, required_free_bytes=2**63 - 1)
    request = OtaUpdateRequest(uuid4(), upload_id, digest, "0.2.0-rc.7")

    receipt = OtaUpdateEngine(host_paths, runtime).apply(request)

    assert receipt.phase == "failed"
    assert receipt.error == "ota_insufficient_space"
    assert runtime.calls == []
    assert upload.is_file(), "failed preflight must preserve the verified upload for retry"


def test_admission_os_error_is_not_exposed_in_public_ota_status(host_paths):
    request = request_for(host_paths)
    engine = OtaUpdateEngine(host_paths, FakeRuntime())
    marker = "internal_location_marker"

    def fail_admission(**_kwargs):
        raise OSError(marker)

    engine.store.admit = fail_admission

    receipt = engine.apply(request)
    public_status = (host_paths.ops / "public" / "ota-status.json").read_text()

    assert receipt.phase == "failed"
    assert receipt.error == "ota_admission_failed"
    assert marker not in public_status


@pytest.mark.parametrize("target", ["journal", "receipt"])
def test_malformed_durable_ota_state_never_starts_update(host_paths, target):
    request = request_for(host_paths)
    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    path = engine.journal_path if target == "journal" else engine._receipt_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]")

    with pytest.raises(RuntimeError, match="ota_state_invalid"):
        engine.apply(request)

    assert runtime.calls == []


def test_incomplete_terminal_ota_journal_never_starts_new_update(host_paths):
    request = request_for(host_paths)
    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    engine.journal_path.parent.mkdir(parents=True, exist_ok=True)
    engine.journal_path.write_text('{"phase":"published"}')

    with pytest.raises(RuntimeError, match="ota_state_invalid"):
        engine.apply(request)

    assert runtime.calls == []


def test_receipt_for_another_operation_cannot_complete_ota(host_paths):
    request = request_for(host_paths)
    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    atomic_write_json(engine._receipt_path(request), {
        "operation_id": str(uuid4()),
        "version": request.version,
        "sha256": request.sha256,
        "phase": "published",
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "error": None,
    })

    with pytest.raises(RuntimeError, match="ota_state_invalid"):
        engine.apply(request)

    assert runtime.calls == []


def test_cleanup_failure_after_publish_keeps_durable_success_receipt(host_paths):
    runtime = FakeRuntime(fail_at="cleanup")
    engine = OtaUpdateEngine(host_paths, runtime)
    request = request_for(host_paths)

    receipt = engine.apply(request)

    assert receipt.phase == "published"
    assert receipt.error is None
    assert engine._existing_receipt(request) == receipt
    warning = json.loads(
        (
            host_paths.state
            / "ota-cleanup-warnings"
            / f"{request.operation_id}.json"
        ).read_text()
    )
    assert warning == {
        "operation_id": str(request.operation_id),
        "warning": "ota_cleanup_failed",
    }


@pytest.mark.parametrize("replay", ["recover", "apply"])
def test_terminal_receipt_replays_interrupted_runtime_cleanup(host_paths, replay):
    class PowerLoss(BaseException):
        pass

    class InterruptedCleanup(FakeRuntime):
        interrupted = False

        def cleanup(self, request, package):
            self._call("cleanup")
            if not self.interrupted:
                self.interrupted = True
                raise PowerLoss()

    request = request_for(host_paths)
    runtime = InterruptedCleanup()
    engine = OtaUpdateEngine(host_paths, runtime)

    with pytest.raises(PowerLoss):
        engine.apply(request)

    assert engine._existing_receipt(request).phase == "published"
    if replay == "recover":
        assert engine.recover() is None
    else:
        assert engine.apply(request).phase == "published"
    assert runtime.calls.count("cleanup") == 2


def test_terminal_receipt_never_cleans_old_operation_when_journal_changed(host_paths):
    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    old_request = request_for(host_paths)
    assert engine.apply(old_request).phase == "published"
    calls = list(runtime.calls)
    new_request = request_for(host_paths, version="0.2.0-rc.8")
    engine._write_journal(new_request, "published")

    assert engine.apply(old_request).phase == "published"
    assert runtime.calls == calls


def test_cleanup_failure_after_successful_rollback_does_not_block_next_ota(host_paths):
    class MigrationAndCleanupFailure(FakeRuntime):
        def migrate(self, request, package):
            self._call("migrate")
            raise RuntimeError("migrate")

        def cleanup(self, request, package):
            self._call("cleanup")
            raise RuntimeError("cleanup")

    request = request_for(host_paths)
    engine = OtaUpdateEngine(host_paths, MigrationAndCleanupFailure())

    receipt = engine.apply(request)

    assert receipt.phase == "rolled_back"
    assert receipt.error == "ota_migrate_failed"
    warning = json.loads(
        (host_paths.state / "ota-cleanup-warnings" / f"{request.operation_id}.json").read_text()
    )
    assert warning["warning"] == "ota_cleanup_failed"
    assert OtaUpdateEngine(host_paths, FakeRuntime()).apply(
        request_for(host_paths, version="0.2.0-rc.8")
    ).phase == "published"


def test_two_consecutive_updates_release_both_verified_packages(host_paths):
    engine = OtaUpdateEngine(host_paths, FakeRuntime())
    for version in ("0.2.0-rc.7", "0.2.0-rc.8"):
        request = request_for(host_paths, version=version)
        receipt = engine.apply(request)
        assert receipt.phase == "published"
        assert not (host_paths.ops / "ota-uploads" / f"{request.upload_id}.ota").exists()
        assert not (host_paths.state / "ota-packages" / f"{request.sha256}.ota").exists()


def test_second_ota_receipt_starts_at_its_own_operation_time(host_paths, monkeypatch):
    from robopark_host import ota_update

    engine = OtaUpdateEngine(host_paths, FakeRuntime())
    monkeypatch.setattr(ota_update, "_now", lambda: "2026-09-25T09:00:00+00:00")
    first = engine.apply(request_for(host_paths, version="0.2.0-rc.7"))
    monkeypatch.setattr(ota_update, "_now", lambda: "2026-09-25T10:00:00+00:00")
    second = engine.apply(request_for(host_paths, version="0.2.0-rc.8"))

    assert first.started_at == "2026-09-25T09:00:00+00:00"
    assert second.started_at == "2026-09-25T10:00:00+00:00"


def test_interrupted_pre_migration_update_keeps_package_until_terminal_receipt(
    host_paths,
):
    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    request = request_for(host_paths)
    engine.store.admit(
        upload_id=request.upload_id,
        expected_sha256=request.sha256,
        expected_version=request.version,
        current_version=runtime.current_version(),
    )
    cache = host_paths.state / "ota-packages" / f"{request.sha256}.ota"
    engine._write_journal(request, "staged")
    assert cache.exists()
    assert not (host_paths.ops / "ota-uploads" / f"{request.upload_id}.ota").exists()

    receipt = engine.recover()
    assert receipt.phase == "rolled_back"
    assert runtime.calls == ["abort_snapshot", "resume", "cleanup"]
    assert not cache.exists()


def test_recovery_rolls_back_verified_update_even_when_disk_is_full(
    host_paths, monkeypatch
):
    from robopark_host import ota_store

    runtime = FakeRuntime()
    engine = OtaUpdateEngine(host_paths, runtime)
    request = request_for(host_paths)
    engine.store.admit(
        upload_id=request.upload_id,
        expected_sha256=request.sha256,
        expected_version=request.version,
        current_version=runtime.current_version(),
    )
    engine._write_journal(request, "migration_started")
    monkeypatch.setattr(
        ota_store.shutil, "disk_usage",
        lambda _path: SimpleNamespace(total=20 * 1024**3, free=0),
    )

    receipt = engine.recover()

    assert receipt.phase == "rolled_back"
    assert runtime.calls == ["rollback", "resume", "cleanup"]


def test_failed_rollback_keeps_package_for_manual_recovery(host_paths):
    runtime = FakeRuntime(fail_at="rollback")
    engine = OtaUpdateEngine(host_paths, runtime)
    request = request_for(host_paths)
    receipt = engine.apply(request)
    assert receipt.phase == "published"

    failed = request_for(host_paths, version="0.2.0-rc.8")
    engine._write_journal(failed, "migration_started")
    cache = host_paths.state / "ota-packages" / f"{failed.sha256}.ota"
    engine.store.admit(
        upload_id=failed.upload_id,
        expected_sha256=failed.sha256,
        expected_version=failed.version,
        current_version=None,
    )
    recovery = engine.recover()
    assert recovery.error == "ota_rollback_failed"
    assert cache.exists()


def test_failed_rollback_blocks_next_update_and_preserves_recovery_state(host_paths):
    runtime = FakeRuntime(fail_at="rollback")
    engine = OtaUpdateEngine(host_paths, runtime)
    failed = request_for(host_paths)
    engine.store.admit(
        upload_id=failed.upload_id,
        expected_sha256=failed.sha256,
        expected_version=failed.version,
        current_version=None,
    )
    engine._write_journal(failed, "migration_started")
    assert engine.recover().error == "ota_rollback_failed"
    journal_before = engine.journal_path.read_bytes()
    cache = host_paths.state / "ota-packages" / f"{failed.sha256}.ota"
    next_request = request_for(host_paths, version="0.2.0-rc.8")

    with pytest.raises(RuntimeError, match="ota_rollback_failed"):
        engine.apply(next_request)

    assert engine.journal_path.read_bytes() == journal_before
    assert cache.is_file()
    assert (host_paths.ops / "ota-uploads" / f"{next_request.upload_id}.ota").is_file()


@pytest.mark.parametrize("failure", ["migrate", "health_check", "publish"])
def test_update_rolls_back_after_snapshot_failure(host_paths, failure):
    runtime = FakeRuntime(fail_at=failure)
    request = request_for(host_paths)

    receipt = OtaUpdateEngine(host_paths, runtime).apply(request)

    assert receipt.phase == "rolled_back"
    assert receipt.error == f"ota_{failure}_failed"
    assert runtime.calls[-3:] == ["rollback", "resume", "cleanup"]


def test_update_rejects_concurrent_operation_and_conflicting_replay(host_paths):
    runtime = FakeRuntime()
    request = request_for(host_paths)
    engine = OtaUpdateEngine(host_paths, runtime)
    engine._write_journal(request, "staged")

    with pytest.raises(RuntimeError, match="ota_update_in_progress"):
        engine.apply(request_for(host_paths))

    conflicting = OtaUpdateRequest(
        operation_id=request.operation_id,
        upload_id=request.upload_id,
        sha256="0" * 64,
        version=request.version,
    )
    with pytest.raises(RuntimeError, match="ota_operation_conflict"):
        engine.apply(conflicting)


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("accepted", ["cleanup"]),
        ("verified", ["cleanup"]),
        ("staged", ["abort_snapshot", "resume", "cleanup"]),
        ("snapshot_done", ["abort_snapshot", "resume", "cleanup"]),
        ("migration_started", ["rollback", "resume", "cleanup"]),
        ("migration_done", ["rollback", "resume", "cleanup"]),
        ("cutover_started", ["rollback", "resume", "cleanup"]),
        ("health_checked", ["rollback", "resume", "cleanup"]),
    ],
)
def test_recovery_is_decided_from_durable_phase(host_paths, phase, expected):
    runtime = FakeRuntime()
    request = request_for(host_paths)
    engine = OtaUpdateEngine(host_paths, runtime)
    engine._write_journal(request, phase)

    receipt = engine.recover()

    assert receipt.phase in {"failed", "rolled_back"}
    assert runtime.calls == expected


def test_request_strictly_validates_identifiers_digest_and_version():
    request = OtaUpdateRequest.from_dict(
        {
            "operation_id": str(uuid4()),
            "upload_id": str(uuid4()),
            "sha256": "a" * 64,
            "version": "0.2.0-rc.7",
        }
    )
    assert str(request.operation_id)
    with pytest.raises(ValueError, match="invalid_ota_update_request"):
        OtaUpdateRequest.from_dict({**request.as_dict(), "sha256": "../bad"})


class CandidateRunner:
    def __init__(self, *, ready=True, fail_down=False):
        self.ready = ready
        self.fail_down = fail_down
        self.commands = []
        self.readiness = []
        self.builder_created = False

    def run(self, argv, **kwargs):
        self.commands.append((list(map(str, argv)), kwargs))
        if argv[:3] == ["docker", "buildx", "inspect"]:
            if not self.builder_created:
                raise FileNotFoundError("builder absent")
            return f"Name: {argv[-1]}\nDriver: docker-container\n".encode()
        if argv[:3] == ["docker", "buildx", "create"]:
            self.builder_created = True
            return b""
        if argv[:3] == ["docker", "inspect", "--type"]:
            name = argv[-1].removeprefix("buildx_buildkit_").removesuffix("0")
            return json.dumps([f"ROBOPARK_BUILDER_OWNER={name}"]).encode()
        if self.fail_down and "down" in argv:
            raise RuntimeError("candidate_down_failed")
        return b""

    def wait_ready(self, **kwargs):
        self.readiness.append(kwargs)
        return self.ready


def test_cutover_provisions_recovery_key_before_restarting_host_services(
    monkeypatch, host_paths
):
    from robopark_host import updater

    request = request_for(host_paths)
    current = "0.2.0-rc.6-current"
    candidate = f"{request.version}-{request.operation_id}"
    (host_paths.releases / current).mkdir(parents=True)
    (host_paths.releases / candidate / "deploy/host").mkdir(parents=True)
    compose = host_paths.state / "compose/previous.json"
    atomic_write_json(compose, {"services": {}})
    atomic_write_json(host_paths.state / "compose" / f"{request.operation_id}-production.json", {"services": {}})
    atomic_write_json(host_paths.state / "ota-runtime" / f"{request.operation_id}.json", {
        "schema": 1,
        "operation_id": str(request.operation_id),
        "candidate": candidate,
        "current": current,
        "previous": None,
        "compose": "compose/previous.json",
    })
    monkeypatch.setattr(updater, "_activate_system_files", lambda *_args: None)
    runner = CandidateRunner()

    SystemOtaUpdateRuntime(host_paths, runner).cutover(request, SimpleNamespace())

    key = host_paths.etc / "backup-recovery.key"
    assert key.is_file()
    assert len(key.read_bytes()) == 32
    assert runner.commands[-1][0] == ["systemctl", "restart", "robopark.service"]


def test_resume_restores_tuna_after_published_or_rolled_back_update(monkeypatch, host_paths):
    from robopark_host import updater

    request = request_for(host_paths)
    order = []

    class Runner(CandidateRunner):
        def run(self, argv, **kwargs):
            order.append(argv[1])
            return b"enabled\n" if argv[1] == "show" else b"active\n"

    monkeypatch.setattr(updater, "_maintenance", lambda _paths, _value: order.append("resume_writes"))
    SystemOtaUpdateRuntime(host_paths, Runner()).resume(request)
    assert order == ["resume_writes", "show", "reset-failed", "start", "is-active", "compose"]


def test_tuna_restart_failure_does_not_revert_published_data(monkeypatch, host_paths):
    from robopark_host import updater

    class Runner(CandidateRunner):
        def run(self, argv, **kwargs):
            if argv[1] == "show":
                return b"enabled\n"
            raise RuntimeError("service_start_failed")

    monkeypatch.setattr(updater, "_maintenance", lambda *_args: None)
    request = request_for(host_paths)
    SystemOtaUpdateRuntime(host_paths, Runner()).resume(request)
    status = json.loads((host_paths.ops / "public/host-status.json").read_text())
    assert status["publication"] == "degraded"
    assert status["publication_error"] == "ota_tuna_restart_failed"


@pytest.mark.parametrize(
    "previous_tmpfiles_contents",
    ["d /run/robopark-old 0750 root root -\n", None],
)
@pytest.mark.parametrize("older_state", ["none", "present", "retired"])
def test_system_rollback_restores_public_release_status_and_tmpfiles(
    monkeypatch, host_paths, previous_tmpfiles_contents, older_state
):
    from robopark_host import rollback, updater

    request = request_for(host_paths)
    identity = str(request.operation_id)
    previous = host_paths.releases / "0.2.0-rc.6"
    candidate = host_paths.releases / f"{request.version}-{identity}"
    previous.mkdir(parents=True)
    candidate.mkdir()
    older = host_paths.releases / "0.2.0-rc.5"
    if older_state == "present":
        older.mkdir()
    (previous / "deploy/host").mkdir(parents=True)
    previous_tmpfiles = previous / "deploy/tmpfiles.d/robopark.conf"
    if previous_tmpfiles_contents is not None:
        previous_tmpfiles.parent.mkdir(parents=True)
        previous_tmpfiles.write_text(previous_tmpfiles_contents)
    installed_tmpfiles = host_paths.root / "etc/tmpfiles.d/robopark.conf"
    installed_tmpfiles.parent.mkdir(parents=True)
    installed_tmpfiles.write_text("d /run/robopark-candidate 0750 root root -\n")
    host_paths.current.symlink_to(candidate)
    host_paths.previous.symlink_to(previous)
    compose = host_paths.state / "compose/original.json"
    compose.parent.mkdir(parents=True)
    atomic_write_json(compose, {"services": {}})
    (host_paths.state / "current-compose.json").symlink_to(compose)
    old_status = {"version": "0.2.0-rc.6", "database_head": "old-head"}
    atomic_write_json(
        host_paths.ops / "public/release-status.json",
        {"version": request.version, "database_head": "candidate-head"},
        mode=0o644,
    )
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1,
            "operation_id": identity,
            "candidate": candidate.name,
            "current": previous.name,
            "previous": None if older_state == "none" else older.name,
            "compose": compose.relative_to(host_paths.state).as_posix(),
            "release_status": old_status,
        },
    )
    monkeypatch.setattr(rollback, "restore_data", lambda *args: None)
    monkeypatch.setattr(rollback, "restore_units", lambda *args: None)
    monkeypatch.setattr(updater, "_maintenance", lambda *args: None)

    marker = host_paths.var / "data/telegram-bot/enabled.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"schema": 1, "enabled": true}\n')
    marker.chmod(0o600)
    runner = CandidateRunner()

    terminal_restored = []
    monkeypatch.setattr(
        "robopark_host.terminal_install.reconcile_terminal_installation",
        lambda paths, release, runner: terminal_restored.append(release),
    )
    SystemOtaUpdateRuntime(host_paths, runner).rollback(request, SimpleNamespace())

    assert terminal_restored == [previous]
    if older_state == "present":
        assert host_paths.previous.resolve(strict=True) == older
    else:
        assert not host_paths.previous.is_symlink()
        assert not host_paths.previous.exists()
    assert json.loads(
        (host_paths.ops / "public/release-status.json").read_text()
    ) == old_status
    if previous_tmpfiles_contents is None:
        assert not installed_tmpfiles.exists()
    else:
        assert installed_tmpfiles.read_text() == previous_tmpfiles_contents
    assert runner.readiness == [{
        "project": "robopark",
        "config": host_paths.state / "current-compose.json",
        "timeout": 180,
        "bot_required": False,
    }]


def test_snapshot_abort_checks_core_while_bot_is_blocked_by_maintenance(host_paths):
    request = request_for(host_paths)
    marker = host_paths.var / "data/telegram-bot/enabled.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"schema": 1, "enabled": true}\n')
    marker.chmod(0o600)
    from robopark_host.updater import _maintenance

    _maintenance(host_paths, True)

    class BarrierRunner(CandidateRunner):
        def wait_ready(self, **kwargs):
            assert (host_paths.ops / "public/maintenance.json").exists()
            self.readiness.append(kwargs)
            return not kwargs.get("bot_required", False)

    runner = BarrierRunner()

    SystemOtaUpdateRuntime(host_paths, runner).abort_snapshot(
        request, SimpleNamespace()
    )

    assert runner.readiness == [{
        "project": "robopark",
        "config": host_paths.state / "current-compose.json",
        "timeout": 180,
        "bot_required": False,
    }]


@pytest.mark.parametrize("outcome", ["ready", "timeout", "reconcile_error", "disabled", "invalid"])
@pytest.mark.parametrize("fail_at", [None, "health_check"])
def test_resume_checks_bot_only_after_durable_decision_and_opening_writes(
    host_paths, monkeypatch, outcome, fail_at
):
    from robopark_host.updater import _maintenance

    marker = host_paths.var / "data/telegram-bot/enabled.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"schema": 1, "enabled": outcome != "disabled"}))
    if outcome == "invalid":
        marker.write_text("invalid")
    marker.chmod(0o600)
    request = request_for(host_paths)
    _maintenance(host_paths, True)
    runtime = FakeRuntime(fail_at=fail_at)
    system = SystemOtaUpdateRuntime(host_paths, None)

    class BarrierRunner(CandidateRunner):
        def run(self, argv, **kwargs):
            assert not (host_paths.ops / "public/maintenance.json").exists()
            journal = json.loads((host_paths.state / "ota-update-journal.json").read_text())
            assert journal["phase"] == ("rolled_back" if fail_at else "published")
            if argv[:2] == ["docker", "compose"]:
                assert any(command[0][1] == "is-active" for command in self.commands)
                if outcome == "reconcile_error":
                    raise RuntimeError("docker_unavailable")
            result = super().run(argv, **kwargs)
            if argv[1] == "show":
                return b"enabled\n"
            if argv[1] == "is-active":
                return b"active\n"
            return result

        def wait_ready(self, **kwargs):
            assert not (host_paths.ops / "public/maintenance.json").exists()
            self.readiness.append(kwargs)
            return outcome == "ready"

    runner = BarrierRunner()
    system.runner = runner
    monkeypatch.setattr(runtime, "resume", system.resume)
    receipt = OtaUpdateEngine(host_paths, runtime).apply(request)
    assert receipt.phase == ("rolled_back" if fail_at else "published")
    assert runtime.calls.count("rollback") == bool(fail_at)
    if outcome in {"ready", "timeout"}:
        assert runner.readiness[-1]["bot_required"] is True
    else:
        assert runner.readiness == []
    if outcome in {"disabled", "invalid"}:
        assert runner.commands[-1][0][-4:] == ["stop", "--timeout", "30", "bot"]
    status = json.loads((host_paths.ops / "public/host-status.json").read_text())
    if outcome in {"ready", "disabled"}:
        assert "bot_error" not in status
    else:
        assert status["publication"] == "degraded"
        assert status["bot"] == "degraded"
        assert status["bot_error"] == "ota_bot_resume_failed"


def test_candidate_health_preserves_barrier_and_does_not_wait_for_bot(host_paths, monkeypatch):
    from robopark_host.updater import _maintenance

    _maintenance(host_paths, True)
    monkeypatch.setattr(
        "robopark_host.terminal_install.reconcile_terminal_installation", lambda *args: None
    )
    monkeypatch.setattr(
        "robopark_host.ai_install.reconcile_ai_installation", lambda *args, **kwargs: None
    )

    class BarrierRunner(CandidateRunner):
        def wait_ready(self, **kwargs):
            assert (host_paths.ops / "public/maintenance.json").exists()
            return not kwargs["bot_required"]

    SystemOtaUpdateRuntime(host_paths, BarrierRunner()).health_check(None, None)
    assert (host_paths.ops / "public/maintenance.json").exists()


def _verified_package(host_paths, request):
    return OtaPackageStore(host_paths).admit(
        upload_id=request.upload_id,
        expected_sha256=request.sha256,
        expected_version=request.version,
        current_version="0.2.0-rc.6",
    )


def _stub_candidate_configs(monkeypatch, host_paths):
    from robopark_host import runtime, updater

    def render(paths, journal, runner, staging):
        del runner, staging
        root = paths.state / "compose"
        production = root / f"{journal['job_id']}-production.json"
        smoke = root / f"{journal['job_id']}-smoke.json"
        document = {
            "services": {
                "api": {"image": f"robopark-api:{journal['job_id']}"},
                "web": {"image": f"robopark-web:{journal['job_id']}"},
            }
        }
        atomic_write_json(production, document)
        atomic_write_json(smoke, document)
        return paths.ops / "staging" / journal["job_id"], smoke

    def pin(document, inspect):
        del inspect
        document["services"]["api"]["image"] = "sha256:" + "1" * 64
        document["services"]["web"]["image"] = "sha256:" + "2" * 64

    monkeypatch.setattr(updater, "_render_configs", render)
    monkeypatch.setattr(runtime, "pin_images", pin)


def test_candidate_compose_builds_carry_exact_image_ownership_labels(
    monkeypatch, host_paths,
):
    from robopark_host import runtime, updater

    identity = str(uuid4())
    candidate = f"2.0.0-{identity}"
    stage = host_paths.releases / f".staging-{identity}"
    stage.mkdir(parents=True)
    config = {"services": {
        "db": {},
        "api": {"build": {"context": str(stage / "apps/api")}},
        "web": {"build": {"context": str(stage / "apps/web")}},
        "bot": {"build": {"context": str(stage / "apps/bot")}},
    }}

    class ConfigRunner:
        def run(self, *_args, **_kwargs):
            return json.dumps(config)

    monkeypatch.setattr(runtime, "source_compose_environment", lambda _paths: {})
    monkeypatch.setattr(updater, "production_config", lambda document, *_args, **_kwargs: document)

    _work, smoke = updater._render_configs(
        host_paths, {"job_id": identity, "candidate": candidate}, ConfigRunner(), stage,
    )
    production = host_paths.state / "compose" / f"{identity}-production.json"
    for path in (production, smoke):
        services = json.loads(path.read_text())["services"]
        for name in ("api", "web"):
            assert services[name]["build"]["labels"] == {
                "io.robopark.ota.operation-id": identity,
                "io.robopark.ota.release": candidate,
            }
    assert "bot" not in json.loads(smoke.read_text())["services"]
    assert json.loads(production.read_text())["services"]["bot"]["build"]["context"] == str(stage / "apps/bot")


def test_system_runtime_owns_candidate_images_before_build(monkeypatch, host_paths):
    _stub_candidate_configs(monkeypatch, host_paths)
    request = request_for(host_paths)
    package = _verified_package(host_paths, request)

    runner = CandidateRunner()
    SystemOtaUpdateRuntime(host_paths, runner).stage(request, package)

    builder = json.loads((host_paths.state / "buildkit-builder.json").read_text())["name"]
    builds = [argv for argv, _ in runner.commands if "build" in argv]
    assert len(builds) == 2
    assert all(argv[argv.index("build") + 1:argv.index("build") + 3] == ["--builder", builder] for argv in builds)
    commands = [argv for argv, _ in runner.commands]
    bootstrap = commands.index(["docker", "buildx", "inspect", "--bootstrap", builder])
    owner = commands.index([
        "docker", "inspect", "--type", "container", "--format",
        "{{json .Config.Env}}", "buildx_buildkit_" + builder + "0",
    ])
    first_build = min(index for index, argv in enumerate(commands) if "build" in argv)
    assert bootstrap < owner < first_build

    receipt = json.loads(
        (host_paths.state / "image-owned" / f"{request.operation_id}.json").read_text()
    )
    assert receipt == {
        "schema": 1,
        "release": f"{request.version}-{request.operation_id}",
        "tag": str(request.operation_id),
        "images": {"api": "sha256:" + "1" * 64, "web": "sha256:" + "2" * 64},
    }


def test_system_runtime_fsyncs_releases_after_candidate_rename(
    monkeypatch, host_paths
):
    from robopark_host import rollback

    _stub_candidate_configs(monkeypatch, host_paths)
    request = request_for(host_paths)
    package = _verified_package(host_paths, request)
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
    synced = []

    def record_sync(path):
        assert candidate.is_dir()
        synced.append(path)

    monkeypatch.setattr(rollback, "sync_directory", record_sync)

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).stage(request, package)

    assert synced == [host_paths.releases]


def test_system_runtime_fsyncs_staging_directories_before_candidate_rename(
    monkeypatch, host_paths
):
    from robopark_host import ota_update, updater

    _stub_candidate_configs(monkeypatch, host_paths)
    render = updater._render_configs
    request = request_for(host_paths)
    package = _verified_package(host_paths, request)
    staging = host_paths.releases / f".staging-{request.operation_id}"
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
    synced = []

    def render_with_nested_file(paths, journal, runner, stage):
        nested = stage / "nested"
        nested.mkdir()
        (nested / "payload").write_text("payload")
        return render(paths, journal, runner, stage)

    def record_sync(path):
        assert not candidate.exists()
        if path == host_paths.state:
            assert (
                host_paths.state
                / "ota-staged-candidates"
                / f"{request.operation_id}.json"
            ).is_file()
            synced.append("state")
        else:
            assert (staging / "manifest.json").is_file()
            synced.append(path.relative_to(staging).as_posix())

    monkeypatch.setattr(updater, "_render_configs", render_with_nested_file)
    monkeypatch.setattr(
        ota_update, "_sync_directory_no_follow", record_sync, raising=False
    )

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).stage(request, package)

    assert synced == ["nested", ".", "state"]
    assert candidate.is_dir()


def test_system_runtime_rejects_symlinked_staging_entry(monkeypatch, host_paths):
    from robopark_host import updater

    _stub_candidate_configs(monkeypatch, host_paths)
    render = updater._render_configs
    request = request_for(host_paths)
    package = _verified_package(host_paths, request)
    outside = host_paths.var / "outside"
    outside.write_text("keep")

    def render_with_symlink(paths, journal, runner, stage):
        (stage / "payload").symlink_to(outside)
        return render(paths, journal, runner, stage)

    monkeypatch.setattr(updater, "_render_configs", render_with_symlink)

    with pytest.raises(RuntimeError, match="ota_unsafe_staging"):
        SystemOtaUpdateRuntime(host_paths, CandidateRunner()).stage(request, package)

    assert outside.read_text() == "keep"


def test_system_runtime_reserves_tags_before_interrupted_build(monkeypatch, host_paths):
    _stub_candidate_configs(monkeypatch, host_paths)
    request = request_for(host_paths)
    package = _verified_package(host_paths, request)

    class PowerLoss(BaseException):
        pass

    class InterruptedBuild(CandidateRunner):
        def run(self, argv, **kwargs):
            if "build" in argv:
                receipt = host_paths.state / "image-owned" / f"{request.operation_id}.json"
                assert json.loads(receipt.read_text())["images"] is None
                raise PowerLoss()
            return super().run(argv, **kwargs)

    with pytest.raises(PowerLoss):
        SystemOtaUpdateRuntime(host_paths, InterruptedBuild()).stage(request, package)

    assert (host_paths.state / "image-owned" / f"{request.operation_id}.json").exists()


def test_recovery_removes_owned_candidate_after_rename_before_staged_phase(
    monkeypatch, host_paths
):
    _stub_candidate_configs(monkeypatch, host_paths)
    request = request_for(host_paths)
    current = host_paths.releases / "0.2.0-rc.6"
    current.mkdir(parents=True)
    (current / "manifest.json").write_text('{"app_version":"0.2.0-rc.6"}')
    host_paths.current.symlink_to(current)
    runtime = SystemOtaUpdateRuntime(host_paths, CandidateRunner())
    engine = OtaUpdateEngine(host_paths, runtime)
    write_journal = engine._write_journal
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"

    class PowerLoss(BaseException):
        pass

    def interrupt_before_staged_phase(pending_request, phase, **extra):
        if phase == "staged":
            assert candidate.is_dir()
            raise PowerLoss()
        return write_journal(pending_request, phase, **extra)

    monkeypatch.setattr(engine, "_write_journal", interrupt_before_staged_phase)

    with pytest.raises(PowerLoss):
        engine.apply(request)

    assert json.loads(engine.journal_path.read_text())["phase"] == "verified"
    assert not (host_paths.state / "ota-runtime" / f"{request.operation_id}.json").exists()
    assert candidate.is_dir()

    receipt = engine.recover()

    assert receipt.phase == "failed"
    assert receipt.error == "ota_interrupted"
    assert not candidate.exists()
    assert not (
        host_paths.state / "ota-staged-candidates" / f"{request.operation_id}.json"
    ).exists()


def test_system_runtime_removes_exact_candidate_project_when_smoke_fails(
    monkeypatch, host_paths
):
    _stub_candidate_configs(monkeypatch, host_paths)
    request = request_for(host_paths)
    package = _verified_package(host_paths, request)
    runner = CandidateRunner(ready=False)

    with pytest.raises(RuntimeError, match="ota_smoke_failed"):
        SystemOtaUpdateRuntime(host_paths, runner).stage(request, package)

    down = [argv for argv, _ in runner.commands if "down" in argv]
    assert len(down) == 1
    assert f"robopark-candidate-{request.operation_id}" in down[0]
    assert down[0][-3:] == ["down", "--volumes", "--remove-orphans"]


def test_system_runtime_keeps_cleanup_config_when_candidate_down_fails(host_paths):
    request = request_for(host_paths)
    smoke = host_paths.state / "compose" / f"{request.operation_id}-smoke.json"
    atomic_write_json(smoke, {"services": {}})
    runtime = SystemOtaUpdateRuntime(host_paths, CandidateRunner(fail_down=True))

    with pytest.raises(RuntimeError, match="ota_candidate_cleanup_failed"):
        runtime.cleanup(request, SimpleNamespace())

    assert smoke.is_file()


def test_cleanup_does_not_claim_unreceipted_candidate_directory(host_paths):
    request = request_for(host_paths)
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
    candidate.mkdir(parents=True)
    (candidate / "keep").write_text("unknown owner")

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
        request, SimpleNamespace()
    )

    assert (candidate / "keep").read_text() == "unknown owner"


def test_cleanup_rejects_tampered_staged_candidate_receipt(host_paths):
    request = request_for(host_paths)
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
    candidate.mkdir(parents=True)
    receipt = (
        host_paths.state / "ota-staged-candidates" / f"{request.operation_id}.json"
    )
    atomic_write_json(
        receipt,
        {
            "schema": 1,
            "operation_id": str(request.operation_id),
            "candidate": "different-candidate",
        },
    )

    with pytest.raises(RuntimeError, match="ota_staging_state_invalid"):
        SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
            request, SimpleNamespace()
        )

    assert candidate.is_dir()
    assert receipt.is_file()


def test_cleanup_rejects_staged_candidate_receipt_through_parent_symlink(
    host_paths
):
    request = request_for(host_paths)
    active = host_paths.releases / "active"
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
    active.mkdir(parents=True)
    candidate.mkdir()
    host_paths.current.symlink_to(active)
    outside = host_paths.var / "foreign-staging-state"
    outside.mkdir(parents=True)
    receipt_parent = host_paths.state / "ota-staged-candidates"
    receipt_parent.parent.mkdir(parents=True, exist_ok=True)
    receipt_parent.symlink_to(outside, target_is_directory=True)
    atomic_write_json(
        outside / f"{request.operation_id}.json",
        {
            "schema": 1,
            "operation_id": str(request.operation_id),
            "candidate": candidate.name,
        },
    )

    with pytest.raises(RuntimeError, match="ota_staging_state_invalid"):
        SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
            request, SimpleNamespace()
        )

    assert candidate.is_dir()


@pytest.mark.parametrize("protected_link", ["current", "previous", "recovery"])
def test_receipted_candidate_cleanup_preserves_protected_release(
    host_paths, protected_link
):
    request = request_for(host_paths)
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
    candidate.mkdir(parents=True)
    if protected_link == "current":
        host_paths.current.symlink_to(candidate)
    else:
        active = host_paths.releases / "active"
        active.mkdir()
        host_paths.current.symlink_to(active)
        getattr(host_paths, protected_link).symlink_to(candidate)
    receipt = (
        host_paths.state / "ota-staged-candidates" / f"{request.operation_id}.json"
    )
    atomic_write_json(
        receipt,
        {
            "schema": 1,
            "operation_id": str(request.operation_id),
            "candidate": candidate.name,
        },
    )

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
        request, SimpleNamespace()
    )

    assert candidate.is_dir()


def test_receipted_cleanup_preserves_current_through_releases_alias(host_paths):
    request = request_for(host_paths)
    real_releases = host_paths.var / "real-releases"
    real_releases.mkdir(parents=True)
    host_paths.releases.parent.mkdir(parents=True)
    host_paths.releases.symlink_to(real_releases, target_is_directory=True)
    candidate = host_paths.releases / f"{request.version}-{request.operation_id}"
    candidate.mkdir()
    host_paths.current.symlink_to(candidate)
    atomic_write_json(
        host_paths.state
        / "ota-staged-candidates"
        / f"{request.operation_id}.json",
        {
            "schema": 1,
            "operation_id": str(request.operation_id),
            "candidate": candidate.name,
        },
    )

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
        request, SimpleNamespace()
    )

    assert candidate.is_dir()


def test_failed_system_cleanup_removes_only_its_snapshot_and_displaced_data(host_paths):
    request = request_for(host_paths)
    identity = str(request.operation_id)
    current = host_paths.releases / "0.2.0-rc.6"
    previous = host_paths.releases / "0.2.0-rc.5"
    candidate = host_paths.releases / f"{request.version}-{identity}"
    for release in (current, previous, candidate):
        release.mkdir(parents=True)
    host_paths.current.symlink_to(current)
    host_paths.previous.symlink_to(previous)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1,
            "operation_id": identity,
            "candidate": candidate.name,
            "current": current.name,
            "previous": previous.name,
            "compose": "compose/current.json",
        },
    )
    snapshot = host_paths.ops / "rollbacks" / identity
    displaced = host_paths.var / f".displaced-{identity}"
    foreign_snapshot = host_paths.ops / "rollbacks" / str(uuid4())
    foreign_displaced = host_paths.var / f".displaced-{uuid4()}"
    for artifact in (snapshot, displaced, foreign_snapshot, foreign_displaced):
        artifact.mkdir(parents=True)
    work = host_paths.ops / "staging" / identity
    foreign_work = host_paths.ops / "staging" / str(uuid4())
    for directory in (work, foreign_work):
        directory.mkdir(parents=True)
        (directory / "test.env").write_text("isolated")

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
        request, SimpleNamespace()
    )

    assert not snapshot.exists()
    assert not displaced.exists()
    assert not work.exists()
    assert current.is_dir() and previous.is_dir()
    assert foreign_snapshot.is_dir() and foreign_displaced.is_dir()
    assert foreign_work.is_dir()


@pytest.mark.parametrize("protected_link", ["previous", "recovery"])
def test_replayed_cleanup_preserves_candidate_referenced_by_recovery_links(
    host_paths, protected_link
):
    request = request_for(host_paths)
    identity = str(request.operation_id)
    active = host_paths.releases / "active"
    candidate = host_paths.releases / f"{request.version}-{identity}"
    active.mkdir(parents=True)
    candidate.mkdir()
    host_paths.current.symlink_to(active)
    getattr(host_paths, protected_link).symlink_to(candidate)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1,
            "operation_id": identity,
            "candidate": candidate.name,
            "current": active.name,
            "previous": None,
            "compose": "compose/current.json",
        },
    )
    snapshot = host_paths.ops / "rollbacks" / identity
    snapshot.mkdir(parents=True)
    success = host_paths.state / "successful-releases" / f"{candidate.name}.json"
    atomic_write_json(success, {"successful": True})

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
        request, SimpleNamespace()
    )

    assert candidate.is_dir()
    assert snapshot.is_dir()
    assert success.is_file()


def test_replayed_cleanup_stops_on_broken_recovery_link(host_paths):
    request = request_for(host_paths)
    identity = str(request.operation_id)
    active = host_paths.releases / "active"
    candidate = host_paths.releases / f"{request.version}-{identity}"
    active.mkdir(parents=True)
    candidate.mkdir()
    host_paths.current.symlink_to(active)
    host_paths.previous.symlink_to(host_paths.releases / "missing")
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1, "operation_id": identity,
            "candidate": candidate.name, "current": active.name,
            "previous": None, "compose": "compose/current.json",
        },
    )
    snapshot = host_paths.ops / "rollbacks" / identity
    snapshot.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="ota_unsafe_release_link"):
        SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
            request, SimpleNamespace()
        )
    assert candidate.is_dir()
    assert snapshot.is_dir()


def test_replayed_cleanup_rejects_symlinked_candidate(host_paths):
    request = request_for(host_paths)
    identity = str(request.operation_id)
    active = host_paths.releases / "active"
    active.mkdir(parents=True)
    host_paths.current.symlink_to(active)
    outside = host_paths.var / "outside"
    outside.mkdir(parents=True)
    (outside / "keep").write_text("keep")
    candidate = host_paths.releases / f"{request.version}-{identity}"
    candidate.symlink_to(outside, target_is_directory=True)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1, "operation_id": identity,
            "candidate": candidate.name, "current": active.name,
            "previous": None, "compose": "compose/current.json",
        },
    )
    snapshot = host_paths.ops / "rollbacks" / identity
    snapshot.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="ota_unsafe_candidate"):
        SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
            request, SimpleNamespace()
        )
    assert (outside / "keep").read_text() == "keep"
    assert snapshot.is_dir()


def test_rollback_cleanup_removes_only_exact_candidate_success_receipt(host_paths):
    request = request_for(host_paths)
    identity = str(request.operation_id)
    current = host_paths.releases / "0.2.0-rc.6"
    rollback = host_paths.releases / "0.2.0-rc.5"
    candidate = host_paths.releases / f"{request.version}-{identity}"
    for release in (current, rollback, candidate):
        release.mkdir(parents=True)
    host_paths.current.symlink_to(current)
    host_paths.previous.symlink_to(rollback)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1,
            "operation_id": identity,
            "candidate": candidate.name,
            "current": current.name,
            "previous": rollback.name,
            "compose": "compose/current.json",
        },
    )
    receipts = host_paths.state / "successful-releases"
    for release in (current, rollback, candidate):
        atomic_write_json(receipts / f"{release.name}.json", {"successful": True})

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
        request, SimpleNamespace()
    )

    assert not candidate.exists()
    assert not (receipts / f"{candidate.name}.json").exists()
    assert (receipts / f"{current.name}.json").is_file()
    assert (receipts / f"{rollback.name}.json").is_file()


def test_ota_runtime_rejects_metadata_paths_outside_owned_roots(host_paths):
    request = request_for(host_paths)
    outside = host_paths.var / "outside"
    outside.mkdir(parents=True)
    (outside / "keep").write_text("keep")
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{request.operation_id}.json",
        {
            "schema": 1,
            "operation_id": str(request.operation_id),
            "candidate": "../../../var/lib/robopark/outside",
            "current": "0.2.0-rc.6",
            "previous": None,
            "compose": "compose/current.json",
        },
    )
    runtime = SystemOtaUpdateRuntime(host_paths, CandidateRunner())

    with pytest.raises(RuntimeError, match="ota_runtime_state_invalid"):
        runtime._metadata(request)
    runtime.cleanup(request, SimpleNamespace())
    assert (outside / "keep").read_text() == "keep"


def test_successful_system_cleanup_keeps_current_snapshot_for_rollback(host_paths):
    request = request_for(host_paths)
    identity = str(request.operation_id)
    current = host_paths.releases / f"{request.version}-{identity}"
    previous = host_paths.releases / "0.2.0-rc.6"
    current.mkdir(parents=True)
    previous.mkdir()
    host_paths.current.symlink_to(current)
    host_paths.previous.symlink_to(previous)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1,
            "operation_id": identity,
            "candidate": current.name,
            "current": previous.name,
            "previous": None,
            "compose": "compose/current.json",
        },
    )
    snapshot = host_paths.ops / "rollbacks" / identity
    snapshot.mkdir(parents=True)

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).cleanup(
        request, SimpleNamespace()
    )

    assert snapshot.is_dir()
    assert current.is_dir() and previous.is_dir()


def test_failed_update_releases_its_unneeded_snapshot_and_compose_config(
    host_paths, monkeypatch
):
    from robopark_host import updater

    identity = str(uuid4())
    current = host_paths.releases / "current-release"
    candidate = host_paths.releases / f"failed-release-{identity}"
    current.mkdir(parents=True)
    candidate.mkdir()
    host_paths.current.symlink_to(current)
    host_paths.previous.symlink_to(current)
    config_dir = host_paths.state / "compose"
    config_dir.mkdir(parents=True, exist_ok=True)
    current_config = config_dir / "current.json"
    failed_config = config_dir / f"{identity}-production.json"
    current_config.write_text("{}")
    failed_config.write_text("{}")
    (host_paths.state / "current-compose.json").symlink_to(current_config)
    snapshot = host_paths.ops / "rollbacks" / identity
    snapshot.mkdir(parents=True)
    (snapshot / "database.dump").write_bytes(b"snapshot")
    monkeypatch.setattr(updater, "_cleanup_staging", lambda *_args: None)
    monkeypatch.setattr(updater, "cleanup_images", lambda *_args: None)
    monkeypatch.setattr(updater, "_finish", lambda *_args: "finished")
    journal = {
        "job_id": identity, "candidate": candidate.name,
        "previous_config": "compose/current.json", "error": "migration_failed",
    }

    assert updater._failed_housekeeping(host_paths, journal, SimpleNamespace()) == "finished"
    assert not candidate.exists()
    assert not snapshot.exists()
    assert not failed_config.exists()
    assert current.is_dir() and current_config.is_file()


def test_second_system_publish_retires_only_owned_release_outside_current_rollback(
    monkeypatch, host_paths
):
    from robopark_host import image_retention, updater

    request = request_for(host_paths, version="0.2.0-rc.8")
    identity = str(request.operation_id)
    old = f"0.2.0-rc.6-{uuid4()}"
    rollback = f"0.2.0-rc.7-{uuid4()}"
    current = f"{request.version}-{identity}"
    for name in (old, rollback, current, "foreign"):
        (host_paths.releases / name).mkdir(parents=True)
    host_paths.current.symlink_to(host_paths.releases / current)
    host_paths.previous.symlink_to(host_paths.releases / rollback)
    compose = host_paths.state / "compose"
    previous_config = compose / "previous-production.json"
    current_config = compose / f"{identity}-production.json"
    retired_config = compose / f"{old[-36:]}-production.json"
    foreign_config = compose / "foreign.json"
    atomic_write_json(previous_config, {"services": {}})
    atomic_write_json(current_config, {"services": {}})
    atomic_write_json(retired_config, {"services": {}})
    atomic_write_json(foreign_config, {"services": {}})
    retired_snapshot = host_paths.ops / "rollbacks" / old[-36:]
    foreign_snapshot = host_paths.ops / "rollbacks" / str(uuid4())
    retired_snapshot.mkdir(parents=True)
    foreign_snapshot.mkdir(parents=True)
    (host_paths.state / "current-compose.json").symlink_to(current_config)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1,
            "operation_id": identity,
            "candidate": current,
            "current": rollback,
            "previous": old,
            "compose": previous_config.relative_to(host_paths.state).as_posix(),
        },
    )
    for name in (old, rollback):
        atomic_write_json(
            host_paths.state / "successful-releases" / f"{name}.json",
            {"successful": True},
        )
    monkeypatch.setattr(updater, "_maintenance", lambda *args: None)
    monkeypatch.setattr(updater, "_publish_status", lambda *args: None)
    cleanup = []
    monkeypatch.setattr(
        image_retention,
        "maintenance",
        lambda paths, runner, *, enforce_builder_budget=False: cleanup.append(
            enforce_builder_budget
        ),
    )
    package = SimpleNamespace(
        sha256="a" * 64,
        manifest=SimpleNamespace(
            app_version=request.version,
            git_sha="b" * 40,
            migration_head="0050_media_action_dependency",
        ),
    )

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).publish(request, package)

    assert not (host_paths.releases / old).exists()
    assert (host_paths.releases / rollback).is_dir()
    assert (host_paths.releases / current).is_dir()
    assert (host_paths.releases / "foreign").is_dir()
    assert not retired_config.exists()
    assert previous_config.is_file()
    assert current_config.is_file()
    assert foreign_config.is_file()
    assert not retired_snapshot.exists()
    assert foreign_snapshot.is_dir()
    assert cleanup == [True]
    assert not (host_paths.state / "successful-ota" / f"{package.sha256}.json").exists()


def test_retention_keeps_release_receipt_when_exact_config_cleanup_fails(
    monkeypatch, host_paths
):
    from robopark_host import updater

    current = f"0.2.0-rc.8-{uuid4()}"
    rollback = f"0.2.0-rc.7-{uuid4()}"
    old = f"0.2.0-rc.6-{uuid4()}"
    current_release = host_paths.releases / current
    rollback_release = host_paths.releases / rollback
    old_release = host_paths.releases / old
    current_release.mkdir(parents=True)
    rollback_release.mkdir(parents=True)
    old_release.mkdir(parents=True)
    host_paths.current.symlink_to(current_release)
    host_paths.previous.symlink_to(rollback_release)
    config = host_paths.state / "compose" / f"{old[-36:]}-production.json"
    atomic_write_json(config, {"services": {}})
    receipt = host_paths.state / "successful-releases" / f"{old}.json"
    atomic_write_json(receipt, {"successful": True})
    original_unlink = type(config).unlink

    def fail_config_unlink(path, *args, **kwargs):
        if path == config:
            raise OSError("simulated interrupted cleanup")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(config), "unlink", fail_config_unlink)
    with pytest.raises(OSError, match="simulated interrupted cleanup"):
        updater._retention(
            host_paths,
            {"job_id": str(uuid4()), "previous_config": "compose/current.json"},
        )

    assert old_release.is_dir()
    assert receipt.is_file()


def test_retention_failure_does_not_roll_back_healthy_published_release(
    monkeypatch, host_paths
):
    from robopark_host import image_retention, updater

    request = request_for(host_paths)
    identity = str(request.operation_id)
    previous = f"0.2.0-rc.6-{uuid4()}"
    candidate = f"{request.version}-{identity}"
    for name in (previous, candidate):
        (host_paths.releases / name).mkdir(parents=True)
    compose = host_paths.state / "compose" / "previous.json"
    atomic_write_json(compose, {"services": {}})
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1,
            "operation_id": identity,
            "candidate": candidate,
            "current": previous,
            "previous": None,
            "compose": compose.relative_to(host_paths.state).as_posix(),
        },
    )
    monkeypatch.setattr(updater, "_maintenance", lambda *args: None)
    monkeypatch.setattr(updater, "_publish_status", lambda *args: None)
    monkeypatch.setattr(updater, "_retention", lambda *args: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(image_retention, "maintenance", lambda *args, **kwargs: {})
    package = SimpleNamespace(
        sha256="a" * 64,
        manifest=SimpleNamespace(
            app_version=request.version,
            git_sha="b" * 40,
            migration_head="0050_media_action_dependency",
        ),
    )

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).publish(request, package)


def test_image_cleanup_failure_does_not_roll_back_healthy_published_release(
    monkeypatch, host_paths
):
    from robopark_host import image_retention, updater

    request = request_for(host_paths)
    identity = str(request.operation_id)
    previous = f"0.2.0-rc.6-{uuid4()}"
    candidate = f"{request.version}-{identity}"
    for name in (previous, candidate):
        (host_paths.releases / name).mkdir(parents=True)
    compose = host_paths.state / "compose" / "previous.json"
    atomic_write_json(compose, {"services": {}})
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{identity}.json",
        {
            "schema": 1,
            "operation_id": identity,
            "candidate": candidate,
            "current": previous,
            "previous": None,
            "compose": compose.relative_to(host_paths.state).as_posix(),
        },
    )
    monkeypatch.setattr(updater, "_maintenance", lambda *args: None)
    monkeypatch.setattr(updater, "_publish_status", lambda *args: None)
    monkeypatch.setattr(updater, "_retention", lambda *args: None)
    monkeypatch.setattr(
        image_retention,
        "maintenance",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("docker unavailable")),
    )
    package = SimpleNamespace(
        sha256="a" * 64,
        manifest=SimpleNamespace(
            app_version=request.version,
            git_sha="b" * 40,
            migration_head="0050_media_action_dependency",
        ),
    )

    SystemOtaUpdateRuntime(host_paths, CandidateRunner()).publish(request, package)

    warning = json.loads(
        (host_paths.state / "ota-cleanup-warnings" / f"{identity}.json").read_text()
    )
    assert warning == {"operation_id": identity, "warning": "ota_image_cleanup_failed"}
    retention = json.loads((host_paths.state / "image-retention.json").read_text())
    assert retention["blocked"] is True
    from robopark_host import retention as storage_retention
    from robopark_host.doctor import _artifact_check

    monkeypatch.setattr(
        storage_retention,
        "artifact_usage",
        lambda paths: {"blocked": False, "pressure": False, "bytes": 0, "files": 0},
    )
    assert _artifact_check(host_paths, CandidateRunner()).status == "failed"


def test_stage_pulls_changed_database_image_before_resolving_image_ids(monkeypatch, host_paths):
    from robopark_host import runtime, updater
    _stub_candidate_configs(monkeypatch, host_paths)
    render = updater._render_configs
    def with_new_database(paths, journal, runner, staging):
        result = render(paths, journal, runner, staging)
        path = paths.state / 'compose' / f"{journal['job_id']}-production.json"
        document = json.loads(path.read_text())
        document['services']['db'] = {'image': 'postgres:17.11-alpine'}
        atomic_write_json(path, document)
        return result
    monkeypatch.setattr(updater, '_render_configs', with_new_database)
    runner = CandidateRunner()
    original_pin = runtime.pin_images
    def pin_after_pull(document, inspect):
        assert any(argv[-2:] == ['pull', 'db'] for argv, _ in runner.commands), 'new database image must exist before pin'
        document['services']['db']['image'] = 'sha256:'+'3'*64
        original_pin(document, inspect)
    monkeypatch.setattr(runtime, 'pin_images', pin_after_pull)
    request = request_for(host_paths)
    SystemOtaUpdateRuntime(host_paths, runner).stage(request, _verified_package(host_paths, request))


def test_rollback_recreates_database_to_remove_post_snapshot_objects(host_paths):
    from robopark_host.rollback import restore_data

    identity = str(uuid4())
    snapshot = host_paths.ops / "rollbacks" / identity
    (snapshot / "data").mkdir(parents=True)
    (snapshot / "data" / "kept.txt").write_text("old data")
    (snapshot / "database.dump").write_bytes(b"fixture dump")
    runner = CandidateRunner()

    restore_data(host_paths, {"job_id": identity}, runner)

    command = runner.commands[0][0]
    assert "--create" in command
    assert "--clean" in command
    assert "--exit-on-error" in command
    assert "--dbname=postgres" in command
    assert command[-1] == f"/host-rollbacks/{identity}/database.dump"
    assert (host_paths.var / "data" / "kept.txt").read_text() == "old data"
