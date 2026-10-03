from __future__ import annotations

import contextlib
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

from .verify import verify_ota


def restart_enabled_tuna(runner, *, wait: bool = True) -> str:
    """Requires= stops ingress with core; restore only explicitly enabled ingress.

    `show` returns disabled state without the nonzero exit of `is-enabled`.
    Never read or rewrite Tuna credentials, and never enable a disabled unit.
    Core ExecStartPost queues the start without waiting: Tuna has After=core.
    Start is idempotent, so finalization does not disconnect a running tunnel.
    """
    try:
        state = runner.run(
            ["systemctl", "show", "robopark-tuna.service", "--property=UnitFileState", "--value"],
            timeout=15, capture=True,
        )
        if isinstance(state, bytes):
            state = state.decode("ascii")
        if not isinstance(state, str):
            return "failed"
        if state.strip() not in {"enabled", "enabled-runtime"}:
            return "disabled"
        runner.run(["systemctl", "reset-failed", "robopark-tuna.service"], timeout=15)
        command = ["systemctl", "start"]
        if not wait:
            command.append("--no-block")
        runner.run(command + ["robopark-tuna.service"], timeout=150 if wait else 15)
        if not wait:
            return "queued"
        active = runner.run(["systemctl", "is-active", "robopark-tuna.service"], timeout=15, capture=True)
        return "active" if active in {b"active\n", b"active", "active\n", "active"} else "failed"
    except (OSError, RuntimeError, ValueError):
        return "failed"


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
        runtime = SystemOtaUpdateRuntime(paths, runner)
        from robopark_host.state import exclusive_lock
        try:
            from robopark_host.terminal_install import quiesce_terminal
        except ModuleNotFoundError as exc:
            if exc.name != "robopark_host.terminal_install":
                raise
            # Frozen pre-foundation runtimes have no terminal to quiesce.
        else:
            quiesce_terminal(paths, runner, reason="ota")
        with exclusive_lock(paths.host_lock):
            receipt = OtaUpdateEngine(paths, runtime).apply(
                OtaUpdateRequest(
                    operation_id=operation_id,
                    upload_id=upload_id,
                    sha256=package.sha256,
                    version=package.manifest.app_version,
                )
            )
        result = receipt.as_dict()
        if receipt.phase in {"published", "rolled_back"}:
            # This invocation still uses the previous release's updater. Older
            # updaters never restarted Requires= ingress after stopping core.
            tuna_state = getattr(runtime, "tuna_state", None) or restart_enabled_tuna(runner)
            if tuna_state == "failed":
                from robopark_host.updater import _publish_status

                _publish_status(paths, {
                    "state": "current_healthy", "error": None,
                    "job_id": str(operation_id), "publication": "degraded",
                    "publication_error": "ota_tuna_restart_failed",
                })
                result["publication"] = "degraded"
        if receipt.phase != "published":
            raise RuntimeError(receipt.error or "ota_update_failed")
        return result
    finally:
        if upload is not None:
            with contextlib.suppress(OSError):
                upload.unlink()
        if sys.path and sys.path[0] == str(host_tools):
            sys.path.pop(0)
