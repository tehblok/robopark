from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from robopark_api.services.ops.ota_uploads import OtaUploadStore
from robopark_host.ota_update import OtaUpdateEngine, OtaUpdateRequest
from robopark_ota.credentials import RoyalCredentials, credential_file
from robopark_ota.install import CleanInstallCoordinator
from test_ota_store import make_ota


@dataclass
class InstallRuntime:
    calls: list[str] = field(default_factory=list)

    def __getattr__(self, name):
        if name == "collect_diagnostics":
            return lambda: Path("/tmp/diagnostics.zip")

        def call(*_args):
            self.calls.append(name)

        return call


@dataclass
class UpdateRuntime:
    fail_at: str | None = None
    calls: list[str] = field(default_factory=list)

    def current_version(self):
        return "0.2.0-rc.6"

    def __getattr__(self, name):
        def call(*_args):
            self.calls.append(name)
            if name == self.fail_at:
                raise RuntimeError(name)

        return call


def test_unified_ota_acceptance_preserves_contracts_and_rolls_back(host_paths, tmp_path):
    install = InstallRuntime()
    secret = RoyalCredentials("royal", "Correct-Password-42!")
    with credential_file(secret, tmp_path / "secrets") as path:
        CleanInstallCoordinator(install).run(path)
    assert install.calls[-1] == "publish"
    assert not list((tmp_path / "secrets").glob("seed-*.json"))

    source = tmp_path / "source.ota"
    digest = make_ota(source)
    content = source.read_bytes()
    assert hashlib.sha256(content).hexdigest() == digest
    upload_store = OtaUploadStore(tmp_path / "uploads", host_paths.ops / "ota-uploads")
    upload = upload_store.create(
        actor_id=1, filename="robopark.ota", size=len(content), sha256=digest
    )
    split = len(content) // 2
    offset = upload_store.append(
        upload.upload_id, actor_id=1, offset=0, chunk=content[:split]
    )
    assert upload_store.status(upload.upload_id, actor_id=1).offset == offset
    upload_store.append(
        upload.upload_id, actor_id=1, offset=offset, chunk=content[offset:]
    )
    finalized = upload_store.finalize(upload.upload_id, actor_id=1)
    assert finalized.sha256 == digest

    operation_id = uuid4()
    request = OtaUpdateRequest(
        operation_id=operation_id,
        upload_id=upload.upload_id,
        sha256=digest,
        version=finalized.version,
    )
    runtime = UpdateRuntime()
    receipt = OtaUpdateEngine(host_paths, runtime).apply(request)
    assert (receipt.phase, receipt.sha256, receipt.version) == (
        "published",
        digest,
        finalized.version,
    )

    failed_id = uuid4()
    failed_upload = host_paths.ops / "ota-uploads" / f"{failed_id}.ota"
    failed_digest = make_ota(failed_upload)
    failed = OtaUpdateEngine(host_paths, UpdateRuntime(fail_at="migrate")).apply(
        OtaUpdateRequest(uuid4(), failed_id, failed_digest, "0.2.0-rc.7")
    )
    assert failed.phase == "rolled_back"
    assert failed.error == "ota_migrate_failed"
