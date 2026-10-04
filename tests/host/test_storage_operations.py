import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _storage_failure(monkeypatch, code="storage_mount_missing"):
    from robopark_host.storage_layout import StorageError

    def fail(*_args, **_kwargs):
        raise StorageError(code)

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", fail)


def _managed_storage(monkeypatch):
    monkeypatch.setattr(
        "robopark_host.storage_layout.require_storage",
        lambda *_args, **_kwargs: {"state": "ready", "mode": "emmc-nvme-data"},
    )


def _compatible_release(path: Path) -> None:
    marker = path / "deploy/storage-layout-version"
    marker.parent.mkdir(parents=True)
    marker.write_text("1\n", encoding="ascii")
    package = path / "deploy/host/robopark_host"
    package.mkdir(parents=True)
    (package / "storage_layout.py").write_text("# packaged guard\n")
    (package / "storage_compatibility.py").write_text("# packaged compatibility gate\n")
    (package / "storage_setup.py").write_text("# packaged setup\n")
    (package / "storage_watchdog.py").write_text("# packaged watchdog\n")


def test_release_gate_keeps_legacy_hosts_compatible_without_marker(host_paths, tmp_path):
    from robopark_host.storage_compatibility import require_storage_release

    candidate = tmp_path / "legacy-release"
    candidate.mkdir()

    assert require_storage_release(host_paths, candidate) == {"state": "unmanaged"}


@pytest.mark.parametrize("marker", [None, "", "2\n", "1", "1\nextra\n"])
def test_release_gate_rejects_managed_candidate_without_exact_capability(
    host_paths, tmp_path, monkeypatch, marker
):
    from robopark_host.release import ReleaseError
    from robopark_host.storage_compatibility import require_storage_release

    _managed_storage(monkeypatch)
    candidate = tmp_path / "candidate"
    package = candidate / "deploy/host/robopark_host"
    package.mkdir(parents=True)
    (package / "storage_layout.py").write_text("# packaged guard\n")
    (package / "storage_compatibility.py").write_text("# packaged compatibility gate\n")
    (package / "storage_setup.py").write_text("# packaged setup\n")
    (package / "storage_watchdog.py").write_text("# packaged watchdog\n")
    if marker is not None:
        path = candidate / "deploy/storage-layout-version"
        path.write_text(marker, encoding="ascii")

    with pytest.raises(ReleaseError, match="^storage_release_incompatible$"):
        require_storage_release(host_paths, candidate)


def test_release_gate_accepts_managed_candidate_with_exact_capability(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host.storage_compatibility import require_storage_release

    _managed_storage(monkeypatch)
    candidate = tmp_path / "candidate"
    _compatible_release(candidate)

    assert require_storage_release(host_paths, candidate)["mode"] == "emmc-nvme-data"


def test_operation_gate_validates_container_roots_only_for_managed_layout(
    host_paths, monkeypatch
):
    from robopark_host.storage_compatibility import require_storage_operations

    checked = []
    monkeypatch.setattr(
        "robopark_host.storage_setup.require_managed_container_roots",
        lambda root: checked.append(root),
        raising=False,
    )
    monkeypatch.setattr(
        "robopark_host.storage_layout.require_storage",
        lambda *_args, **_kwargs: {"state": "unmanaged"},
    )
    assert require_storage_operations(host_paths) == {"state": "unmanaged"}
    assert checked == []

    monkeypatch.setattr(
        "robopark_host.storage_layout.require_storage",
        lambda *_args, **_kwargs: {"state": "ready", "mode": "emmc-nvme-data"},
    )
    require_storage_operations(host_paths)
    assert checked == [host_paths.root]


def test_release_gate_and_guard_refresh_ignore_only_space_for_recovery(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host.storage_compatibility import (
        refresh_storage_release_guard,
        require_storage_release,
    )
    from robopark_host.storage_layout import StorageError

    candidate = tmp_path / "candidate"
    _compatible_release(candidate)
    checks = []
    refreshed = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    monkeypatch.setattr(
        "robopark_host.storage_setup.refresh_storage_guard",
        lambda root, release: refreshed.append((root, release)),
    )

    require_storage_release(host_paths, candidate, check_space=False)
    refresh_storage_release_guard(host_paths, candidate, check_space=False)

    assert checks == [False, False]
    assert refreshed == [(host_paths.root, candidate)]


def test_managed_system_file_activation_refreshes_independent_guard(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host.updater import _activate_system_files

    _managed_storage(monkeypatch)
    candidate = tmp_path / "candidate"
    _compatible_release(candidate)
    refreshed = []
    monkeypatch.setattr(
        "robopark_host.storage_setup.refresh_storage_guard",
        lambda root, release: refreshed.append((root, release)),
        raising=False,
    )

    _activate_system_files(host_paths, candidate)

    assert refreshed == [(host_paths.root, candidate)]


def test_runtime_bootstrap_checks_storage_before_creating_state(host_paths, monkeypatch):
    from robopark_host.runtime import bootstrap_compose
    from robopark_host.storage_layout import StorageError

    _storage_failure(monkeypatch)

    with pytest.raises(StorageError, match="storage_mount_missing"):
        bootstrap_compose(host_paths, lambda _command: pytest.fail("runner reached"))

    assert not host_paths.state.exists()


def test_command_consumer_checks_storage_before_claim_or_projection(host_paths, monkeypatch):
    from robopark_host.commands import consume_commands
    from robopark_host.storage_layout import StorageError

    _storage_failure(monkeypatch)

    with pytest.raises(StorageError, match="storage_mount_missing"):
        consume_commands(host_paths, None, None)

    assert not host_paths.ops.exists()
    assert not host_paths.lock_dir.exists()


@pytest.mark.parametrize(
    ("kind", "expected_check_space"),
    [("cleanup-execute", False), ("backup", True)],
)
def test_typed_cleanup_can_run_when_only_free_space_check_fails(
    host_paths, monkeypatch, kind, expected_check_space
):
    from robopark_host.commands import OperationKind, execute_typed_operation
    from robopark_host.storage_layout import StorageError

    checks = []

    def fail_after_recording(_root, **kwargs):
        checks.append(kwargs["check_space"])
        raise StorageError("storage_space_low")

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", fail_after_recording)
    monkeypatch.setattr(
        "robopark_host.commands.validate_typed_operation",
        lambda *_args, **_kwargs: SimpleNamespace(kind=OperationKind(kind)),
    )

    with pytest.raises(StorageError, match="storage_space_low"):
        execute_typed_operation(host_paths, {}, SimpleNamespace())

    assert checks == [expected_check_space]
    assert not host_paths.ops.exists()


def test_cleanup_host_operation_ignores_only_the_free_space_threshold(
    host_paths, monkeypatch
):
    from robopark_host.state import host_operation
    from robopark_host.storage_layout import StorageError

    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)

    with host_operation(host_paths, check_space=False):
        pass

    assert checks == [False]


def test_typed_ota_durable_dispatch_recovers_when_space_is_low(
    host_paths, monkeypatch
):
    from robopark_host.commands import OperationKind, execute_typed_operation
    from robopark_host.state import atomic_write_json
    from robopark_host.storage_layout import StorageError

    identity = str(uuid4())
    request = {"capability_revision": "revision-1"}
    operation = SimpleNamespace(
        kind=OperationKind.OTA_UPDATE,
        operation_id=identity,
        request=request,
        payload={},
        actor_user_id=7,
    )
    atomic_write_json(
        host_paths.state / "typed-operation-dispatch" / f"{identity}.json",
        {"schema": 1, "request": request, "state": "dispatched"},
    )
    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    effects = SimpleNamespace(
        reconcile=lambda _operation: {
            "state": "succeeded",
            "detail": {"recovered": True},
            "error": None,
        }
    )
    monkeypatch.setattr("robopark_host.commands.validate_typed_operation", lambda *_args, **_kwargs: operation)
    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    monkeypatch.setattr(
        "robopark_host.operation_capabilities.operation_capabilities",
        lambda _effects: {OperationKind.OTA_UPDATE.value: {"available": True}},
    )
    monkeypatch.setattr(
        "robopark_host.operation_capabilities.current_capability_revision",
        lambda *_args: "revision-1",
    )

    result = execute_typed_operation(host_paths, {}, effects)

    assert result["state"] == "succeeded"
    assert result["detail"] == {"recovered": True}
    assert checks and all(check is False for check in checks)


def test_fresh_typed_ota_admission_still_enforces_free_space(host_paths, monkeypatch):
    from robopark_host.commands import OperationKind, execute_typed_operation
    from robopark_host.storage_layout import StorageError

    identity = str(uuid4())
    operation = SimpleNamespace(
        kind=OperationKind.OTA_UPDATE,
        operation_id=identity,
        request={"capability_revision": "revision-1"},
        payload={},
        actor_user_id=7,
    )
    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        raise StorageError("storage_space_low")

    monkeypatch.setattr("robopark_host.commands.validate_typed_operation", lambda *_args, **_kwargs: operation)
    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)

    with pytest.raises(StorageError, match="storage_space_low"):
        execute_typed_operation(host_paths, {}, SimpleNamespace())

    assert checks == [True]
    assert not host_paths.ops.exists()


@pytest.mark.parametrize("kind", ["rollback", "backup-restore"])
def test_typed_rollback_and_matching_restore_journal_can_recover_low_space(
    host_paths, monkeypatch, kind
):
    from robopark_host.commands import OperationKind, execute_typed_operation
    from robopark_host.state import atomic_write_json
    from robopark_host.storage_layout import StorageError

    operation_kind = OperationKind(kind)
    identity = str(uuid4())
    request = {"capability_revision": "revision-1"}
    operation = SimpleNamespace(
        kind=operation_kind,
        operation_id=identity,
        request=request,
        payload={"release": "0.1.0"} if kind == "rollback" else {"backup_id": str(uuid4())},
        actor_user_id=7,
    )
    if kind == "backup-restore":
        atomic_write_json(
            host_paths.state / "restore-journal.json",
            {
                "schema": 2,
                "request": {
                    "kind": "restore",
                    "job_id": identity,
                    "artifact": f"restore-{identity}.zip",
                    "sha256": "a" * 64,
                    "actor_user_id": 7,
                    "created_at": "2026-10-03T00:00:00+00:00",
                },
                "database_profile": "postgresql-17",
                "phase": "replacing",
                "snapshot_done": True,
                "writes_resumed": False,
                "error": None,
                "publication_degraded": False,
            },
        )
    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    effects = SimpleNamespace(
        rollback=lambda *_args: {"rolled_back": True},
        backup_restore=lambda *_args: {"restored": True},
    )
    monkeypatch.setattr("robopark_host.commands.validate_typed_operation", lambda *_args, **_kwargs: operation)
    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    monkeypatch.setattr(
        "robopark_host.operation_capabilities.operation_capabilities",
        lambda _effects: {operation_kind.value: {"available": True}},
    )
    monkeypatch.setattr(
        "robopark_host.operation_capabilities.current_capability_revision",
        lambda *_args: "revision-1",
    )

    result = execute_typed_operation(host_paths, {}, effects)

    assert result["state"] == "succeeded"
    assert checks and all(check is False for check in checks)


def test_hash_ota_checks_storage_before_lock_and_journal(host_paths, monkeypatch):
    from robopark_host.ota_update import OtaUpdateEngine, OtaUpdateRequest
    from robopark_host.storage_layout import StorageError

    _storage_failure(monkeypatch)
    request = OtaUpdateRequest(uuid4(), uuid4(), "a" * 64, "0.2.0-test")

    with pytest.raises(StorageError, match="storage_mount_missing"):
        OtaUpdateEngine(host_paths, SimpleNamespace()).apply(request)

    assert not host_paths.state.exists()
    assert not host_paths.lock_dir.exists()


def test_hash_ota_admission_still_enforces_free_space(host_paths, monkeypatch):
    from robopark_host.ota_update import OtaUpdateEngine, OtaUpdateRequest
    from robopark_host.storage_layout import StorageError

    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    request = OtaUpdateRequest(uuid4(), uuid4(), "a" * 64, "0.2.0-test")

    with pytest.raises(StorageError, match="storage_space_low"):
        OtaUpdateEngine(host_paths, SimpleNamespace()).apply(request)

    assert checks == [False, True]
    assert not (host_paths.state / "ota-journal.json").exists()


def test_hash_ota_low_space_after_stage_still_rolls_back_and_cleans_up(
    host_paths, monkeypatch
):
    from robopark_host.ota_update import OtaUpdateEngine, OtaUpdateRequest
    from robopark_host.storage_layout import StorageError

    low_space = False

    def storage(_root, *, check_space=True, **_kwargs):
        if low_space and check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)

    class Runtime:
        def __init__(self):
            self.calls = []

        def current_version(self):
            return "0.1.0"

        def stage(self, _request, _package):
            nonlocal low_space
            self.calls.append("stage")
            low_space = True

        def snapshot(self, _request, _package):
            self.calls.append("snapshot")
            raise RuntimeError("snapshot failed")

        def abort_snapshot(self, _request, _package):
            self.calls.append("abort_snapshot")

        def resume(self, _request):
            self.calls.append("resume")

        def cleanup(self, _request, _package):
            self.calls.append("cleanup")

    class Store:
        def admit(self, **_kwargs):
            return SimpleNamespace()

        def discard_terminal(self, _sha256):
            return None

    request = OtaUpdateRequest(uuid4(), uuid4(), "a" * 64, "0.2.0-test")
    runtime = Runtime()

    receipt = OtaUpdateEngine(host_paths, runtime, Store()).apply(request)

    assert receipt.phase == "rolled_back"
    assert receipt.error == "ota_snapshot_failed"
    assert runtime.calls == ["stage", "snapshot", "abort_snapshot", "resume", "cleanup"]


def test_restore_checks_storage_before_journal(host_paths, monkeypatch):
    from robopark_host.restore import run_restore
    from robopark_host.storage_layout import StorageError

    _storage_failure(monkeypatch)

    with pytest.raises(StorageError, match="storage_mount_missing"):
        run_restore(host_paths, {"job_id": str(uuid4())}, SimpleNamespace())

    assert not (host_paths.state / "restore-journal.json").exists()


def test_restore_recovery_and_phase_writes_ignore_only_space(host_paths, monkeypatch):
    from robopark_host.commands import _allow_attempt
    from robopark_host.restore import _phase, recover_restore, run_restore
    from robopark_host.state import atomic_write_json
    from robopark_host.storage_layout import StorageError

    identity = str(uuid4())
    request = {
        "kind": "restore",
        "job_id": identity,
        "artifact": f"restore-{identity}.zip",
        "sha256": "a" * 64,
        "actor_user_id": 7,
        "created_at": "2026-10-03T00:00:00+00:00",
    }
    journal = {
        "schema": 2,
        "request": request,
        "database_profile": "postgresql-17",
        "phase": "succeeded",
        "snapshot_done": True,
        "writes_resumed": True,
        "error": None,
        "publication_degraded": False,
    }
    atomic_write_json(host_paths.state / "restore-journal.json", journal)
    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    monkeypatch.setattr("robopark_host.restore_retention.record", lambda *_args: None)

    assert run_restore(host_paths, request, SimpleNamespace())["state"] == "succeeded"
    assert recover_restore(host_paths, SimpleNamespace(), automatic=True) == 0
    assert _allow_attempt(host_paths, request) is True
    _phase(host_paths, journal, "succeeded")

    assert checks and all(check is False for check in checks)


def test_fresh_restore_admission_still_enforces_free_space(host_paths, monkeypatch):
    from robopark_host.restore import run_restore
    from robopark_host.storage_layout import StorageError

    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        raise StorageError("storage_space_low")

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    request = {
        "kind": "restore",
        "job_id": str(uuid4()),
        "artifact": "restore.zip",
        "sha256": "a" * 64,
        "actor_user_id": 7,
        "created_at": "2026-10-03T00:00:00+00:00",
    }

    with pytest.raises(StorageError, match="storage_space_low"):
        run_restore(host_paths, request, SimpleNamespace())

    assert checks == [True]
    assert not (host_paths.state / "restore-journal.json").exists()


def test_rollback_validates_managed_target_before_stopping_service(
    host_paths, monkeypatch
):
    from robopark_host.release import ReleaseError
    from robopark_host.rollback import rollback_release

    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    previous = host_paths.releases / "0.1.0"
    previous.mkdir(parents=True)
    calls = []
    runner = SimpleNamespace(run=lambda command, **_kwargs: calls.append(command))
    journal = {
        "previous": previous.name,
        "writes_resumed": False,
        "migration_started": False,
    }

    monkeypatch.setattr("robopark_host.rollback.verify_directory", lambda *_args: {})
    with pytest.raises(ReleaseError, match="^storage_release_incompatible$"):
        rollback_release(host_paths, journal, runner, lambda *_args, **_kwargs: None)

    assert calls == []
    assert checks == [False]


def test_hash_ota_stage_rejects_missing_marker_before_candidate_commands(
    host_paths, tmp_path, monkeypatch
):
    from robopark_host.ota_update import OtaUpdateRequest, SystemOtaUpdateRuntime
    from robopark_host.release import ReleaseError

    _managed_storage(monkeypatch)
    package_path = tmp_path / "candidate.ota"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("release/deploy/host/robopark_host/storage_layout.py", "# guard\n")
    package = SimpleNamespace(
        path=package_path,
        sha256="a" * 64,
        manifest=SimpleNamespace(
            format_version=1,
            app_version="0.2.0-test",
            git_sha="b" * 40,
            migration_head="0056",
        ),
    )
    request = OtaUpdateRequest(uuid4(), uuid4(), package.sha256, "0.2.0-test")
    runner = SimpleNamespace(run=lambda *_args, **_kwargs: pytest.fail("runner reached"))

    with pytest.raises(ReleaseError, match="^storage_release_incompatible$"):
        SystemOtaUpdateRuntime(host_paths, runner).stage(request, package)

    assert not any(host_paths.releases.glob(".staging-*"))


def test_hash_ota_requires_compatible_rollback_target_before_quiescing(
    host_paths, monkeypatch
):
    from robopark_host.ota_update import OtaUpdateRequest, SystemOtaUpdateRuntime
    from robopark_host.release import ReleaseError

    _managed_storage(monkeypatch)
    current = host_paths.releases / "0.1.0"
    current.mkdir(parents=True)
    host_paths.current.parent.mkdir(parents=True, exist_ok=True)
    host_paths.current.symlink_to(current)
    calls = []
    runner = SimpleNamespace(run=lambda command, **_kwargs: calls.append(command))
    request = OtaUpdateRequest(uuid4(), uuid4(), "a" * 64, "0.2.0-test")

    with pytest.raises(ReleaseError, match="^storage_release_incompatible$"):
        SystemOtaUpdateRuntime(host_paths, runner).snapshot(request, None)

    assert calls == []
    assert not (host_paths.state / "ota-runtime").exists()


def test_hash_ota_cutover_rechecks_candidate_before_switching_links(
    host_paths, monkeypatch
):
    from robopark_host.ota_update import OtaUpdateRequest, SystemOtaUpdateRuntime
    from robopark_host.release import ReleaseError
    from robopark_host.state import atomic_write_json

    _managed_storage(monkeypatch)
    request = OtaUpdateRequest(uuid4(), uuid4(), "a" * 64, "0.2.0-test")
    current = host_paths.releases / "0.1.0"
    candidate = host_paths.releases / f"0.2.0-test-{request.operation_id}"
    current.mkdir(parents=True)
    candidate.mkdir()
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{request.operation_id}.json",
        {
            "schema": 1,
            "operation_id": str(request.operation_id),
            "candidate": candidate.name,
            "current": current.name,
            "previous": None,
            "compose": f"compose/{request.operation_id}-production.json",
        },
    )
    runner = SimpleNamespace(run=lambda *_args, **_kwargs: pytest.fail("runner reached"))

    with pytest.raises(ReleaseError, match="^storage_release_incompatible$"):
        SystemOtaUpdateRuntime(host_paths, runner).cutover(request, None)

    assert not host_paths.current.exists()
    assert not host_paths.previous.exists()


def test_hash_ota_rollback_rechecks_target_before_stopping_service(
    host_paths, monkeypatch
):
    from robopark_host.ota_update import OtaUpdateRequest, SystemOtaUpdateRuntime
    from robopark_host.release import ReleaseError
    from robopark_host.state import atomic_write_json

    _managed_storage(monkeypatch)
    request = OtaUpdateRequest(uuid4(), uuid4(), "a" * 64, "0.2.0-test")
    rollback_target = host_paths.releases / "0.1.0"
    rollback_target.mkdir(parents=True)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{request.operation_id}.json",
        {
            "schema": 1,
            "operation_id": str(request.operation_id),
            "candidate": f"0.2.0-test-{request.operation_id}",
            "current": rollback_target.name,
            "previous": None,
            "compose": f"compose/{request.operation_id}-production.json",
        },
    )
    calls = []
    runner = SimpleNamespace(run=lambda command, **_kwargs: calls.append(command))

    with pytest.raises(ReleaseError, match="^storage_release_incompatible$"):
        SystemOtaUpdateRuntime(host_paths, runner).rollback(request, None)

    assert calls == []


def test_hash_ota_rollback_uses_low_space_recovery_release_gate(
    host_paths, monkeypatch
):
    from robopark_host.ota_update import OtaUpdateRequest, SystemOtaUpdateRuntime
    from robopark_host.state import atomic_write_json

    class ReachedReleaseGate(Exception):
        pass

    request = OtaUpdateRequest(uuid4(), uuid4(), "a" * 64, "0.2.0-test")
    rollback_target = host_paths.releases / "0.1.0"
    rollback_target.mkdir(parents=True)
    atomic_write_json(
        host_paths.state / "ota-runtime" / f"{request.operation_id}.json",
        {
            "schema": 1,
            "operation_id": str(request.operation_id),
            "candidate": f"0.2.0-test-{request.operation_id}",
            "current": rollback_target.name,
            "previous": None,
            "compose": f"compose/{request.operation_id}-production.json",
        },
    )
    storage_checks = []
    release_checks = []
    runtime = SystemOtaUpdateRuntime(host_paths, SimpleNamespace())
    monkeypatch.setattr(
        runtime,
        "_require_storage",
        lambda *, check_space=True: storage_checks.append(check_space),
    )

    def release_gate(_paths, _candidate, *, check_space=True):
        release_checks.append(check_space)
        raise ReachedReleaseGate

    monkeypatch.setattr(
        "robopark_host.storage_compatibility.require_storage_release", release_gate
    )

    with pytest.raises(ReachedReleaseGate):
        runtime.rollback(request, None)

    assert storage_checks == [False]
    assert release_checks == [False]


@pytest.mark.parametrize("method", ["stage", "cutover"])
def test_hash_ota_stage_and_cutover_keep_strict_space_admission(
    host_paths, monkeypatch, method
):
    from robopark_host.ota_update import OtaUpdateRequest, SystemOtaUpdateRuntime
    from robopark_host.storage_layout import StorageError

    checks = []
    runtime = SystemOtaUpdateRuntime(host_paths, SimpleNamespace())

    def storage(*, check_space=True):
        checks.append(check_space)
        raise StorageError("storage_space_low")

    monkeypatch.setattr(runtime, "_require_storage", storage)
    request = OtaUpdateRequest(uuid4(), uuid4(), "a" * 64, "0.2.0-test")

    with pytest.raises(StorageError, match="storage_space_low"):
        getattr(runtime, method)(request, None)

    assert checks == [True]


def test_standard_update_admission_still_enforces_free_space(host_paths, monkeypatch):
    from robopark_host.storage_layout import StorageError
    from robopark_host.updater import apply_release

    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)

    result = apply_release(SimpleNamespace(), host_paths, SimpleNamespace())

    assert result.state == "rejected"
    assert result.error == "storage_space_low"
    assert checks == [True]


def test_standard_recovery_entry_and_journal_writes_ignore_only_space(
    host_paths, monkeypatch
):
    from robopark_host.storage_layout import StorageError
    from robopark_host.updater import _maintenance, _phase, recover_interrupted_update

    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    runner = SimpleNamespace()

    assert recover_interrupted_update(host_paths, runner).state == "idle"
    journal = {"job_id": "job-1"}
    _phase(host_paths, journal, "verified")
    _maintenance(host_paths, True)

    assert checks and all(check is False for check in checks)


@pytest.mark.parametrize(
    "arguments",
    [
        ["update", "--recover"],
        ["update", "--reconcile"],
        ["restore", "--boot-recover"],
    ],
)
def test_cli_recovery_paths_ignore_only_space(host_paths, monkeypatch, arguments):
    from robopark_host import cli
    from robopark_host.storage_layout import StorageError

    checks = []

    def storage(_root, *, check_space=True, **_kwargs):
        checks.append(check_space)
        if check_space:
            raise StorageError("storage_space_low")
        return {"state": "ready", "mode": "emmc-nvme-data"}

    monkeypatch.setattr("robopark_host.storage_layout.require_storage", storage)
    monkeypatch.setattr(
        "robopark_host.updater.recover_interrupted_update",
        lambda *_args, **_kwargs: SimpleNamespace(state="idle"),
    )
    monkeypatch.setattr("robopark_host.restore.recover_restore", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("robopark_host.restore.active_restore", lambda *_args: False)
    monkeypatch.setattr(
        "robopark_host.terminal_install.reconcile_terminal_compose",
        lambda *_args: None,
    )

    assert cli.main(arguments) == 0
    assert checks == [False]


def test_doctor_still_reports_when_storage_is_broken_without_persisting(
    host_paths, monkeypatch
):
    from robopark_host.doctor import run_doctor

    _storage_failure(monkeypatch)
    monkeypatch.setattr(
        "robopark_host.doctor._collect_checks",
        lambda *_args: [],
        raising=False,
    )

    report = run_doctor(host_paths, lambda *_args, **_kwargs: None, SimpleNamespace())

    storage = next(check for check in report.checks if check.code == "storage_layout")
    assert storage.status == "failed"
    assert not (host_paths.var / "diagnostics/latest.json").exists()


def test_terminal_worker_relies_on_root_service_precheck(host_paths, monkeypatch):
    from robopark_host import cli

    _storage_failure(monkeypatch)
    called = []
    monkeypatch.setattr(
        "robopark_host.terminal_worker.run_worker",
        lambda paths, identity, profile: called.append((paths, identity, profile)) or 0,
    )

    assert cli.main(["terminal-worker", "--id", "session-1", "--profile", "maintenance"]) == 0
    assert called == [(host_paths, "session-1", "maintenance")]
