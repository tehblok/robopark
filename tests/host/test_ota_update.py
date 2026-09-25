from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from robopark_host.ota_update import OtaUpdateEngine, OtaUpdateRequest
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

    def rollback(self, request, package):
        self._call("rollback")

    def cleanup(self, request, package):
        self._call("cleanup")


def request_for(host_paths):
    upload_id = uuid4()
    upload = host_paths.ops / "ota-uploads" / f"{upload_id}.ota"
    digest = make_ota(upload)
    return OtaUpdateRequest(
        operation_id=uuid4(),
        upload_id=upload_id,
        sha256=digest,
        version="0.2.0-rc.7",
    )


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
        "snapshot", "stage", "migrate", "cutover", "health_check", "publish", "cleanup"
    ]


@pytest.mark.parametrize("failure", ["migrate", "health_check", "publish"])
def test_update_rolls_back_after_snapshot_failure(host_paths, failure):
    runtime = FakeRuntime(fail_at=failure)
    request = request_for(host_paths)

    receipt = OtaUpdateEngine(host_paths, runtime).apply(request)

    assert receipt.phase == "rolled_back"
    assert receipt.error == f"ota_{failure}_failed"
    assert runtime.calls[-2:] == ["rollback", "cleanup"]


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
        ("snapshot_done", ["rollback", "cleanup"]),
        ("staged", ["rollback", "cleanup"]),
        ("migration_started", ["rollback", "cleanup"]),
        ("migration_done", ["rollback", "cleanup"]),
        ("cutover_started", ["rollback", "cleanup"]),
        ("health_checked", ["rollback", "cleanup"]),
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
