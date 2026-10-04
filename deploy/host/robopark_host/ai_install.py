"""Optional AI payload admission, Compose projection, and service reconciliation."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .ai_runtime import probe_support, publish_status
from .release import ReleaseError, unique_object
from .state import atomic_write_json

AI_UNITS = ("robopark-ai-setup.service", "robopark-ai.service", "robopark-ai-broker.service")
MIGRATION_OPTIONAL_TARGETS = {
    "/run/robopark-terminal", "/run/robopark-ai", "/ops/ai-runtime.json",
    "/ops/ai-public",
}


def migration_compose(paths, config: Path, identity: str) -> Path:
    """Render a migration-only config without optional boot-time bind mounts."""
    document = json.loads(config.read_text())
    volumes = document["services"]["api"].get("volumes", [])
    filtered = [
        volume
        for volume in volumes
        if not (
            isinstance(volume, dict)
            and volume.get("target") in MIGRATION_OPTIONAL_TARGETS
        )
    ]
    if filtered == volumes:
        return config
    document["services"]["api"]["volumes"] = filtered
    target = paths.state / "compose" / f"{identity}-migration.json"
    atomic_write_json(target, document)
    return target


def ai_payload_present(release: Path) -> bool:
    directory = release / "deploy/systemd"
    present = []
    for name in AI_UNITS:
        path = directory / name
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            present.append(False)
            continue
        if directory.is_symlink() or not stat.S_ISREG(mode):
            raise ReleaseError("ai_payload_invalid")
        present.append(True)
    if any(present) and not all(present):
        raise ReleaseError("ai_payload_invalid")
    return all(present)


def reconcile_ai_compose(paths) -> bool:
    release = paths.current.resolve(strict=True)
    if release.parent != paths.releases.resolve(strict=True):
        raise ReleaseError("ai_invalid_release")
    if not ai_payload_present(release):
        return False
    link = paths.state / "current-compose.json"
    target = link.resolve(strict=True)
    if not link.is_symlink() or target.parent != paths.state / "compose":
        raise ReleaseError("ai_invalid_compose")
    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            expected_uid = 0 if paths.root == Path("/") else os.geteuid()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != expected_uid or info.st_nlink != 1 or info.st_size > 1024 * 1024:
                raise ValueError("untrusted")
            document = json.loads(stream.read(), object_pairs_hook=unique_object)
        if document.get("x-robopark-release") != str(release):
            raise ValueError("release")
        services = document["services"]
        socket_mount = {"type": "bind", "source": str(paths.root / "run/robopark-ai"), "target": "/run/robopark-ai", "read_only": True, "bind": {"create_host_path": False}}
        state_mount = {"type": "bind", "source": str(paths.ops / "public"), "target": "/ops/ai-public", "read_only": True, "bind": {"create_host_path": False}}
        changed = False
        expected_environment = {
            "AI_BROKER_SOCKET": "/run/robopark-ai/broker.sock",
            "AI_RUNTIME_STATE_PATH": "/ops/ai-public/ai-runtime.json",
        }
        expected_mounts = (socket_mount, state_mount)
        for service_name, service in services.items():
            if not isinstance(service, dict):
                raise TypeError("service")
            environment = service.get("environment", {})
            volumes = service.get("volumes", [])
            if not isinstance(environment, dict) or not isinstance(volumes, list):
                raise TypeError("service")
            for key, value in expected_environment.items():
                if key in environment and (
                    service_name not in {"api", "worker"} or environment[key] != value
                ):
                    raise ValueError("conflict")
            for volume in volumes:
                encoded = json.dumps(volume, sort_keys=True)
                if "robopark-ai" not in encoded and "ai-runtime.json" not in encoded and "ai-public" not in encoded:
                    continue
                if service_name not in {"api", "worker"} or volume not in expected_mounts:
                    raise ValueError("conflict")
        for name in ("api", "worker"):
            service = services[name]
            environment = service.setdefault("environment", {})
            volumes = service.setdefault("volumes", [])
            for key, value in expected_environment.items():
                if key in environment and environment[key] != value:
                    raise ValueError("conflict")
                if key not in environment:
                    environment[key] = value
                    changed = True
            for mount in (socket_mount, state_mount):
                conflicts = [v for v in volumes if isinstance(v, dict) and v.get("target") == mount["target"]]
                if conflicts and conflicts != [mount]:
                    raise ValueError("conflict")
                if not conflicts:
                    volumes.append(mount)
                    changed = True
        if changed:
            atomic_write_json(target, document, mode=0o600)
        return changed
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReleaseError("ai_invalid_compose") from error


def reconcile_ai_installation(paths, release, runner, *, auto_install=False):
    if not ai_payload_present(release):
        return
    bridge = paths.root / "run/robopark-ai"
    if bridge.is_symlink() or (bridge.exists() and not bridge.is_dir()):
        raise ReleaseError("ai_bridge_invalid")
    bridge.mkdir(parents=True, exist_ok=True, mode=0o750)
    bridge.chmod(0o750)
    if paths.root == Path("/"):
        os.chown(bridge, 0, 10001)
    from .ai_runtime import read_enabled_intent, write_enabled_intent

    enabled_intent = read_enabled_intent(paths)
    supported, reason = probe_support(paths)
    if not supported:
        if enabled_intent:
            runner.run(
                ["systemctl", "disable", "--now", "robopark-ai.service"],
                timeout=60,
            )
        publish_status(paths, supported=False, reason=reason)
        return
    if auto_install and enabled_intent is None:
        write_enabled_intent(paths, True)
        enabled_intent = True
    from .ai_runtime import MODEL_SHA256, installed

    present = installed(paths, verify=not auto_install)
    publish_status(
        paths, supported=True, installed=present, enabled=False, ready=False,
        reason="starting" if present and enabled_intent else "disabled" if present else "not_installed",
        model_sha256=MODEL_SHA256 if present else None,
    )
    runner.run(["systemctl", "daemon-reload"], timeout=30)
    runner.run(
        ["systemctl", "enable", "--now", "robopark-ai-broker.service"],
        timeout=60,
    )
    if auto_install:
        from .ai_runtime import ensure_service_account
        ensure_service_account(paths)
        from .ai_runtime import ensure_api_key
        ensure_api_key(paths)
        if enabled_intent:
            runner.run(["systemctl", "start", "--no-block", "robopark-ai-setup.service"], timeout=30)
    else:
        if present and enabled_intent:
            from .ai_runtime import reconcile
            reconcile(paths, auto_install=False, runner=runner)
        elif present:
            runner.run(
                ["systemctl", "disable", "--now", "robopark-ai.service"],
                timeout=60,
            )
