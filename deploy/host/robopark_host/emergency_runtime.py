"""Publish a private Emergency overlay without mutating release Compose state."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .release import unique_object
from .runtime import production_config, source_compose_environment
from .state import atomic_write_json, exclusive_lock

_MAX_COMPOSE_BYTES = 1024 * 1024
_IMMUTABLE_IMAGE = re.compile(r"sha256:[0-9a-f]{64}")


@dataclass(frozen=True)
class _ActiveCompose:
    release: Path
    tools: Path
    link_text: str
    path: Path
    raw: bytes
    document: dict


def _fail(code: str = "invalid_emergency_runtime") -> None:
    raise ValueError(code)


def _release_paths(paths, expected_release: Path | None) -> tuple[Path, Path]:
    try:
        releases = paths.releases.resolve(strict=True)
        release = paths.current.resolve(strict=True)
        tools_link = paths.opt / "host-tools"
        tools = tools_link.resolve(strict=True)
    except OSError:
        _fail()
    if (
        not paths.current.is_symlink()
        or not (paths.opt / "host-tools").is_symlink()
        or paths.releases.is_symlink()
        or release.is_symlink()
        or not release.is_dir()
        or release.parent != releases
        or tools != release / "deploy/host"
        or tools.is_symlink()
        or not tools.is_dir()
    ):
        _fail()
    if expected_release is not None:
        try:
            expected = Path(expected_release).resolve(strict=True)
        except OSError:
            _fail()
        if expected != release:
            _fail()
    return release, tools


def _read_private(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        _fail()
    try:
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= _MAX_COMPOSE_BYTES
            ):
                _fail()
            raw = stream.read(_MAX_COMPOSE_BYTES + 1)
            if len(raw) != info.st_size:
                _fail()
            return raw
    except OSError:
        _fail()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_document(raw: bytes) -> dict:
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, ValueError, RecursionError):
        _fail()
    if not isinstance(document, dict):
        _fail()
    return document


def _active_compose(paths, expected_release: Path | None) -> _ActiveCompose:
    release, tools = _release_paths(paths, expected_release)
    compose_dir = paths.state / "compose"
    link = paths.state / "current-compose.json"
    try:
        if (
            compose_dir.is_symlink()
            or not compose_dir.is_dir()
            or not link.is_symlink()
        ):
            _fail()
        link_text = os.readlink(link)
        config = link.resolve(strict=True)
    except OSError:
        _fail()
    if config.parent != compose_dir or config.is_symlink():
        _fail()
    raw = _read_private(config)
    document = _parse_document(raw)
    services = document.get("services")
    api = services.get("api") if isinstance(services, dict) else None
    api_image = api.get("image") if isinstance(api, dict) else None
    if (
        document.get("x-robopark-release") != str(release)
        or not isinstance(services, dict)
        or not isinstance(api, dict)
        or not isinstance(api_image, str)
        or _IMMUTABLE_IMAGE.fullmatch(api_image) is None
    ):
        _fail()
    return _ActiveCompose(release, tools, link_text, config, raw, document)


def _run(command: list[str], *, env: dict[str, str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("emergency_compose_validation_failed") from exc
    if len(completed.stdout) > 4 * 1024 * 1024:
        raise ValueError("emergency_compose_validation_failed")
    return completed.stdout


def _compose_environment(paths) -> dict[str, str]:
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "DOCKER_CONFIG": str(paths.var / "ops/docker-config"),
        **source_compose_environment(paths),
    }


def _render_bridge(paths, active: _ActiveCompose, run) -> tuple[dict, dict | None]:
    source = active.release / "deploy/docker-compose.yml"
    try:
        source_info = source.lstat()
    except OSError:
        _fail()
    if (
        not stat.S_ISREG(source_info.st_mode)
        or source_info.st_size > _MAX_COMPOSE_BYTES
    ):
        _fail()
    environment = _compose_environment(paths)
    command = [
        "/usr/bin/docker",
        "compose",
        "--project-name",
        "robopark-emergency-compat",
        "--file",
        str(source),
    ]
    try:
        raw = run(
            [*command, "config", "--format", "json", "--no-env-resolution"],
            env=environment,
        )
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError
        source_document = json.loads(raw, object_pairs_hook=unique_object)
        rendered = production_config(
            source_document,
            paths,
            active.release,
            "emergency-compat",
            source_release=active.release,
        )
        bridge = copy.deepcopy(rendered["services"]["emergency-bridge"])
    except (KeyError, OSError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid_emergency_runtime") from exc
    bridge["image"] = active.document["services"]["api"]["image"]
    dependency = None
    web = rendered.get("services", {}).get("web")
    if isinstance(web, dict):
        depends_on = web.get("depends_on")
        if isinstance(depends_on, dict):
            value = depends_on.get("emergency-bridge")
            if value == {"condition": "service_healthy"}:
                dependency = copy.deepcopy(value)
    return bridge, dependency


def _encoded(document: dict) -> bytes:
    try:
        raw = (
            json.dumps(
                document,
                allow_nan=False,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("invalid_emergency_runtime") from exc
    if len(raw) > _MAX_COMPOSE_BYTES:
        _fail()
    return raw


def _publish_candidate(paths, document: dict) -> tuple[Path, bytes]:
    raw = _encoded(document)
    identity = hashlib.sha256(raw).hexdigest()
    target = paths.state / "compose" / f"emergency-compat-{identity}.json"
    if target.exists() or target.is_symlink():
        if target.is_symlink() or _read_private(target) != raw:
            _fail()
    else:
        atomic_write_json(target, document, mode=0o600)
        if _read_private(target) != raw:
            _fail()
    return target, raw


def _publish_overlay(paths, document: dict, raw: bytes) -> bool:
    target = paths.state / "emergency-compose-overlay.json"
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            _fail()
        existing = _read_private(target)
        if existing == raw:
            return False
    atomic_write_json(target, document, mode=0o600)
    if _read_private(target) != raw:
        _fail()
    return True


def reconcile_emergency_runtime(
    paths,
    *,
    run=_run,
    expected_release: Path | None = None,
) -> bool:
    """Prepare the minimal overlay consumed by the RC44 systemd unit."""

    from .storage_compatibility import require_storage_operations

    lock = paths.lock_dir / "emergency-runtime.lock"
    with exclusive_lock(lock):
        active = _active_compose(paths, expected_release)
        overlay: dict = {"services": {}}
        if "emergency-bridge" not in active.document["services"]:
            bridge, web_dependency = _render_bridge(paths, active, run)
            overlay["services"]["emergency-bridge"] = bridge
            if web_dependency is not None:
                overlay["services"]["web"] = {
                    "depends_on": {"emergency-bridge": web_dependency}
                }
        require_storage_operations(paths, check_space=False)
        candidate, raw = _publish_candidate(paths, overlay)
        validation = [
            "/usr/bin/docker",
            "compose",
            "--project-name",
            "robopark",
            "--file",
            str(paths.state / "current-compose.json"),
            "--file",
            str(candidate),
            "config",
            "--quiet",
        ]
        try:
            run(validation, env=_compose_environment(paths))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise ValueError("emergency_compose_validation_failed") from exc
        current = _active_compose(paths, expected_release)
        if (
            current.release != active.release
            or current.tools != active.tools
            or current.link_text != active.link_text
            or current.path != active.path
            or current.raw != active.raw
        ):
            _fail()
        require_storage_operations(paths, check_space=False)
        return _publish_overlay(paths, overlay, raw)
