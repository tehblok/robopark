from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from .verify import verify_ota


def _stage_upload(source: Path, target: Path, expected_sha256: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.is_symlink() or target.exists() or target.is_symlink():
        raise RuntimeError("ota_unsafe_upload")
    os.chmod(target.parent, 0o700)
    descriptor, name = tempfile.mkstemp(prefix=".local-ota-", dir=target.parent)
    temporary = Path(name)
    digest = hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
            os.fchmod(output.fileno(), 0o600)
            while chunk := input_stream.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != expected_sha256:
            raise RuntimeError("ota_hash_mismatch")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def apply_local_update(bundle: Path, *, root: Path = Path("/")) -> dict:
    if root == Path("/") and os.geteuid() != 0:
        raise PermissionError("root_required")
    package = verify_ota(bundle)
    host_tools = (root / "opt/robopark/host-tools").resolve(strict=True)
    if not host_tools.is_relative_to((root / "opt/robopark/releases").resolve()):
        raise RuntimeError("invalid_host_tools")
    sys.path.insert(0, str(host_tools))
    upload: Path | None = None
    try:
        from robopark_host.ota_update import (
            OtaUpdateEngine,
            OtaUpdateRequest,
            SystemOtaUpdateRuntime,
        )
        from robopark_host.paths import HostPaths
        from robopark_host.updater import SystemRunner

        paths = HostPaths.from_root(root)
        upload_id = uuid4()
        operation_id = uuid4()
        upload = paths.ops / "ota-uploads" / f"{upload_id}.ota"
        _stage_upload(package.path, upload, package.sha256)
        runner = SystemRunner()
        runner.failure_log = paths.root / "var/log/robopark/ota-update.log"
        receipt = OtaUpdateEngine(
            paths, SystemOtaUpdateRuntime(paths, runner)
        ).apply(
            OtaUpdateRequest(
                operation_id=operation_id,
                upload_id=upload_id,
                sha256=package.sha256,
                version=package.manifest.app_version,
            )
        )
        result = receipt.as_dict()
        if receipt.phase != "published":
            raise RuntimeError(receipt.error or "ota_update_failed")
        return result
    finally:
        if upload is not None:
            with contextlib.suppress(OSError):
                upload.unlink()
        if sys.path and sys.path[0] == str(host_tools):
            sys.path.pop(0)
