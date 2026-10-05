"""Pinned, fail-closed local AI runtime lifecycle for verified AGX Orin hosts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import secrets
import shutil
import stat
import subprocess
import threading
import urllib.request
from contextlib import contextmanager, suppress
from pathlib import Path

from .state import atomic_write_json
from .storage_layout import StorageError, require_storage

MODEL_ID = "google/gemma-4-E4B-it-qat-q4_0-gguf"
MODEL_REVISION = "ce70163b5df4580cf3534f9f373f15c6c6a6c4e9"
MODEL_FILE = "gemma-4-E4B_q4_0-it.gguf"
MODEL_SIZE = 5_154_940_864
MODEL_SHA256 = "09f6f2a1d9ff4a1b7db9cc1aad9c55a9df2f5ec133327a92eab593fcf4360ed0"
LLAMA_REPOSITORY = "https://github.com/ggml-org/llama.cpp.git"
LLAMA_COMMIT = "c25030496079fdad724609d94f68b859f25774ce"
LEGACY_MODEL_FILES = ("Ternary-Bonsai-2-27B-PQ2_0.gguf",)


def _read(path: Path, limit: int = 65536) -> bytes:
    data = path.read_bytes()
    if len(data) > limit:
        raise ValueError("hardware_probe_invalid")
    return data.rstrip(b"\0")


def probe_hardware(paths) -> tuple[bool, str | None]:
    """Accept physical AGX Orin identity only; names or RAM alone never suffice."""
    try:
        compatible = _read(paths.root / "proc/device-tree/compatible").decode("ascii", "strict").lower().split("\0")
        meminfo = _read(paths.root / "proc/meminfo").decode("ascii", "strict")
    except (OSError, UnicodeError, ValueError):
        return False, "hardware_identity_unavailable"
    if not any("p3701" in item for item in compatible):
        return False, "p3701_required"
    if not any("tegra234" in item for item in compatible):
        return False, "tegra234_required"
    try:
        memory_kib = int(next(line.split()[1] for line in meminfo.splitlines() if line.startswith("MemTotal:")))
    except (StopIteration, ValueError, IndexError):
        return False, "memory_unknown"
    if memory_kib < 24 * 1024**2:
        return False, "memory_below_24gib"
    return True, None


def probe_support(paths) -> tuple[bool, str | None]:
    supported, reason = probe_hardware(paths)
    if not supported:
        return supported, reason
    try:
        require_storage(paths.root, require_layout=True)
    except StorageError as error:
        return False, error.code
    return True, None


def _status(paths, *, supported, installed=False, enabled=False, ready=False, reason=None, model_sha256=None, backend=None):
    return {
        "schema": 1,
        "supported": bool(supported),
        "installed": bool(installed),
        "enabled": bool(enabled),
        "ready": bool(ready),
        "reason": reason,
        "model": MODEL_ID,
        "model_sha256": model_sha256,
        "backend": backend,
    }


def publish_status(paths, **values):
    payload = _status(paths, **values)
    atomic_write_json(paths.ops / "public/ai-runtime.json", payload, mode=0o644)
    return payload


def _run(argv, *, cwd=None, timeout=1800, input_bytes=None):
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": "/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "C"},
    )
    stdout, stderr = bytearray(), bytearray()

    def drain(stream, retained):
        while chunk := stream.read(4096):
            retained.extend(chunk)
            if len(retained) > 65536:
                del retained[:-65536]

    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    if input_bytes is not None:
        try:
            process.stdin.write(input_bytes)
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise RuntimeError("ai_install_command_timeout") from error
    finally:
        for reader in readers:
            reader.join(timeout=5)
        for stream in (process.stdout, process.stderr):
            stream.close()
    if returncode:
        raise RuntimeError("ai_install_command_failed")
    return bytes(stdout).decode("utf-8", "replace")


@contextmanager
def ai_lock(paths):
    paths.lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(paths.lock_dir / "ai.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def ai_control_lock(paths):
    """Serialize short control decisions without waiting for the setup lock."""
    paths.lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        paths.lock_dir / "ai-control.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def install_runtime(paths) -> None:
    """Resume pinned downloads/builds and atomically publish verified artifacts."""
    base = paths.var / "ai"
    ensure_service_account(paths)
    ensure_api_key(paths)
    models, source, build, binary = base / "models", base / "src/llama.cpp", base / "build", base / "bin/llama-server"
    for directory in (models, source.parent, build, binary.parent):
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    (base / "install.json").unlink(missing_ok=True)
    _run(["nvcc", "--version"], timeout=20)
    if not (source / ".git").is_dir():
        if source.exists():
            shutil.rmtree(source)
        _run(["git", "clone", "--filter=blob:none", "--no-checkout", LLAMA_REPOSITORY, str(source)], timeout=600)
    # Existing installations may contain the retired Prism fork.  Always move
    # the reusable checkout to the pinned upstream before fetching the commit.
    _run(["git", "-C", str(source), "remote", "set-url", "origin", LLAMA_REPOSITORY], timeout=30)
    _run(["git", "-C", str(source), "fetch", "--depth", "1", "origin", LLAMA_COMMIT], timeout=600)
    _run(["git", "-C", str(source), "checkout", "--detach", LLAMA_COMMIT], timeout=60)
    _run([
        "cmake", "-S", str(source), "-B", str(build), "-DGGML_CUDA=ON",
        "-DCMAKE_CUDA_ARCHITECTURES=87", "-DLLAMA_BUILD_SERVER=ON",
        "-DBUILD_SHARED_LIBS=OFF",
        "-DCMAKE_BUILD_TYPE=Release",
    ], timeout=300)
    _run(["cmake", "--build", str(build), "--target", "llama-server", "--parallel", "2"], timeout=3600)
    built = build / "bin/llama-server"
    if not built.is_file():
        raise RuntimeError("ai_binary_missing")
    temporary_binary = binary.with_suffix(".new")
    shutil.copyfile(built, temporary_binary)
    temporary_binary.chmod(0o755)
    os.replace(temporary_binary, binary)

    model = models / MODEL_FILE
    if not (model.is_file() and model.stat().st_size == MODEL_SIZE and _sha256(model) == MODEL_SHA256):
        partial = model.with_suffix(model.suffix + ".part")
        url = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/{MODEL_FILE}"
        _run(["curl", "--fail", "--location", "--proto", "=https", "--tlsv1.2", "--retry", "5", "--continue-at", "-", "--output", str(partial), url], timeout=14400)
        if partial.stat().st_size != MODEL_SIZE or _sha256(partial) != MODEL_SHA256:
            partial.unlink(missing_ok=True)
            raise RuntimeError("ai_model_checksum_mismatch")
        partial.chmod(0o640)
        os.replace(partial, model)
    if paths.root == Path("/"):
        account = pwd.getpwnam("robopark-ai")
        for directory in (base, binary.parent, models):
            os.chown(directory, 0, account.pw_gid)
            directory.chmod(0o750)
        os.chown(base / "bin/llama-server", 0, account.pw_gid)
        os.chown(model, 0, account.pw_gid)
    for legacy_file in LEGACY_MODEL_FILES:
        for suffix in ("", ".part"):
            with suppress(FileNotFoundError):
                (models / (legacy_file + suffix)).unlink()

    atomic_write_json(
        base / "install.json",
        {
            "schema": 2,
            "llama_commit": LLAMA_COMMIT,
            "binary_sha256": _sha256(binary),
            "model_file": MODEL_FILE,
            "model_size": MODEL_SIZE,
            "model_sha256": MODEL_SHA256,
        },
    )


def installed(paths, *, verify=False) -> bool:
    base = paths.var / "ai"
    model = base / "models" / MODEL_FILE
    binary = base / "bin/llama-server"
    if binary.is_symlink() or model.is_symlink() or not binary.is_file() or not model.is_file() or model.stat().st_size != MODEL_SIZE:
        return False
    receipt = base / "install.json"
    try:
        record = json.loads(receipt.read_text())
    except (OSError, ValueError):
        record = None
    binary_hash = record.get("binary_sha256") if isinstance(record, dict) else None
    if not isinstance(binary_hash, str) or len(binary_hash) != 64 or any(c not in "0123456789abcdef" for c in binary_hash):
        return False
    expected = {
        "schema": 2,
        "llama_commit": LLAMA_COMMIT,
        "binary_sha256": binary_hash,
        "model_file": MODEL_FILE,
        "model_size": MODEL_SIZE,
        "model_sha256": MODEL_SHA256,
    }
    if record != expected:
        # A model checksum cannot certify the installed server's build revision.
        # Only install_runtime may write a receipt for a newly built runtime.
        return False
    try:
        return not verify or (_sha256(binary) == binary_hash and _sha256(model) == MODEL_SHA256)
    except OSError:
        return False


def _smoke_tool_response(body) -> bool:
    """A successful HTTP response is not enough to accept a model/template."""
    if not isinstance(body, dict):
        return False
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return False
    if choices[0].get("finish_reason") != "tool_calls":
        return False
    message = choices[0].get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        return False
    call = calls[0]
    function = call.get("function")
    if call.get("type") != "function" or not isinstance(call.get("id"), str) or not call["id"] or not isinstance(function, dict):
        return False
    if function.get("name") != "check_contract" or not isinstance(function.get("arguments"), str):
        return False
    try:
        arguments = json.loads(function["arguments"])
    except ValueError:
        return False
    return isinstance(arguments, dict) and set(arguments) == {"ok"} and arguments["ok"] is True


def runtime_ready(paths) -> tuple[bool, str | None]:
    if not any((paths.root / name).exists() for name in ("dev/nvidia0", "dev/nvhost-gpu")):
        return False, "cuda_device_unavailable"
    try:
        health = urllib.request.Request(
            "http://127.0.0.1:18081/health",
            headers={"Authorization": "Bearer " + read_api_key(paths)},
        )
        with urllib.request.urlopen(health, timeout=3) as response:
            if response.status != 200:
                return False, "health_failed"
        request = urllib.request.Request(
            "http://127.0.0.1:18081/v1/chat/completions",
            # This synthetic function is never dispatched. It checks the actual
            # system/tool template and JSON wire contract before enabling jobs.
            data=json.dumps({
                "messages": [
                    {"role": "system", "content": "Call check_contract once with ok=true. This is a synthetic readiness test."},
                    {"role": "user", "content": "Check the contract now."},
                ],
                "tools": [{"type": "function", "function": {
                    "name": "check_contract", "description": "Synthetic check, no side effects.",
                    "parameters": {"type": "object", "properties": {"ok": {"type": "boolean", "const": True}}, "required": ["ok"], "additionalProperties": False},
                }}],
                "tool_choice": "required",
                "parallel_tool_calls": False, "temperature": 0,
                "max_tokens": 64, "stream": False, "reasoning_effort": "none",
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + read_api_key(paths),
            }, method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                return False, "smoke_failed"
            body = json.loads(raw)
        if response.status != 200 or not _smoke_tool_response(body):
            return False, "smoke_failed"
    except (OSError, ValueError, KeyError):
        return False, "smoke_failed"
    return True, None


def reconcile(paths, *, auto_install=False, runner=None):
    supported, reason = probe_support(paths)
    if not supported:
        return publish_status(paths, supported=False, reason=reason)
    with ai_lock(paths):
        try:
            supported, reason = probe_support(paths)
            if not supported:
                return publish_status(paths, supported=False, reason=reason)
            if auto_install and not installed(paths, verify=True):
                install_runtime(paths)
            present = installed(paths)
            if runner and present:
                runner.run(["systemctl", "enable", "--now", "robopark-ai.service", "robopark-ai-broker.service"], timeout=60)
            ready, why = runtime_ready(paths) if present else (False, "not_installed")
            return publish_status(paths, supported=True, installed=present, enabled=bool(runner and present), ready=ready, reason=why, model_sha256=MODEL_SHA256 if present else None, backend="cuda" if ready else None)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            return publish_status(paths, supported=True, installed=installed(paths), reason="install_failed")


def control(paths, action: str, runner) -> dict:
    if action not in {"enable", "disable", "install", "remove_model"}:
        raise ValueError("ai_control_invalid")
    with ai_control_lock(paths):
        supported, reason = probe_support(paths)
        if action in {"enable", "install"} and not supported:
            return publish_status(paths, supported=False, reason=reason)
        if action == "install":
            ensure_service_account(paths)
            ensure_api_key(paths)
            if read_enabled_intent(paths) is None:
                write_enabled_intent(paths, True)
            runner.run(["systemctl", "start", "--no-block", "robopark-ai-setup.service"], timeout=15)
            return publish_status(paths, supported=True, installed=installed(paths), reason="installing")
        if action == "enable" and not installed(paths, verify=True):
            raise ValueError("ai_not_installed")
        if action in {"disable", "remove_model"}:
            runner.run(["systemctl", "stop", "robopark-ai-setup.service"], timeout=15)
        with ai_lock(paths):
            supported, reason = probe_support(paths)
            if action == "enable":
                if not supported:
                    return publish_status(paths, supported=False, reason=reason)
                ensure_api_key(paths)
                write_enabled_intent(paths, True)
                runner.run(["systemctl", "enable", "--now", "robopark-ai.service", "robopark-ai-broker.service"], timeout=60)
            elif action == "disable":
                write_enabled_intent(paths, False)
                runner.run(["systemctl", "disable", "--now", "robopark-ai.service"], timeout=60)
            else:
                write_enabled_intent(paths, False)
                runner.run(["systemctl", "disable", "--now", "robopark-ai.service"], timeout=60)
                for target in (
                    paths.var / "ai/models" / MODEL_FILE,
                    paths.var / "ai/models" / (MODEL_FILE + ".part"),
                    *(paths.var / "ai/models" / name for name in LEGACY_MODEL_FILES),
                    *(paths.var / "ai/models" / (name + ".part") for name in LEGACY_MODEL_FILES),
                    paths.var / "ai/install.json",
                ):
                    with suppress(FileNotFoundError):
                        target.unlink()
    if not supported:
        return publish_status(paths, supported=False, reason=reason)
    present = installed(paths)
    enabled = action == "enable"
    ready, why = runtime_ready(paths) if enabled and present else (False, "disabled" if present else "not_installed")
    return publish_status(
        paths, supported=True, installed=present, enabled=enabled, ready=ready,
        reason=why, model_sha256=MODEL_SHA256 if present else None,
        backend="cuda" if ready else None,
    )


def refresh_runtime_status(paths, *, enabled: bool, installing: bool = False):
    supported, reason = probe_support(paths)
    if not supported:
        return publish_status(paths, supported=False, reason=reason)
    present = installed(paths)
    if installing and not enabled:
        ready, reason = False, "installing"
    elif enabled and present:
        ready, reason = runtime_ready(paths)
    else:
        ready, reason = False, "disabled" if present else "not_installed"
    return publish_status(
        paths, supported=True, installed=present, enabled=enabled, ready=ready,
        reason=reason, model_sha256=MODEL_SHA256 if present else None,
        backend="cuda" if ready else None,
    )


def ensure_service_account(paths) -> None:
    if paths.root != Path("/"):
        return
    try:
        pwd.getpwnam("robopark-ai")
    except KeyError:
        _run([
            "/usr/sbin/useradd", "--system", "--home-dir", "/var/lib/robopark/ai",
            "--shell", "/usr/sbin/nologin", "--user-group", "robopark-ai",
        ], timeout=30)


def ensure_api_key(paths) -> Path:
    target = paths.var / "ai/api-key"
    if target.is_symlink():
        raise RuntimeError("ai_api_key_invalid")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if not target.exists():
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(secrets.token_urlsafe(48))
            stream.flush()
            os.fsync(stream.fileno())
    info = target.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_mode & 0o777 != 0o600
        or info.st_size not in range(32, 257)
    ):
        raise RuntimeError("ai_api_key_invalid")
    if paths.root == Path("/"):
        account = pwd.getpwnam("robopark-ai")
        os.chown(target, account.pw_uid, account.pw_gid)
    return target


def read_api_key(paths) -> str:
    target = paths.var / "ai/api-key"
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            expected_uid = (
                pwd.getpwnam("robopark-ai").pw_uid
                if paths.root == Path("/")
                else os.geteuid()
            )
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & 0o777 != 0o600
                or info.st_uid != expected_uid
                or info.st_size not in range(32, 257)
            ):
                raise RuntimeError("ai_api_key_invalid")
            value = stream.read(257).decode("ascii", "strict")
    except (OSError, UnicodeError, KeyError) as error:
        raise RuntimeError("ai_api_key_invalid") from error
    if not 32 <= len(value) <= 256 or any(char.isspace() for char in value):
        raise RuntimeError("ai_api_key_invalid")
    return value


def write_enabled_intent(paths, enabled: bool) -> None:
    atomic_write_json(
        paths.var / "ai/control.json",
        {"schema": 1, "enabled": bool(enabled)},
    )


def read_enabled_intent(paths) -> bool | None:
    target = paths.var / "ai/control.json"
    try:
        info = target.lstat()
        expected_uid = 0 if paths.root == Path("/") else os.geteuid()
        if (
            stat.S_ISLNK(info.st_mode)
            or info.st_mode & 0o777 != 0o600
            or info.st_uid != expected_uid
        ):
            return None
        value = json.loads(target.read_text())
    except (OSError, ValueError):
        return None
    if value == {"schema": 1, "enabled": True}:
        return True
    if value == {"schema": 1, "enabled": False}:
        return False
    return None


def activate_intent(paths, runner) -> None:
    """Apply the durable desire after setup; re-read it under the AI lock."""
    with ai_lock(paths):
        runner.run(
            ["systemctl", "enable", "--now", "robopark-ai-broker.service"],
            timeout=60,
        )
        if read_enabled_intent(paths) is True and installed(paths, verify=True):
            ensure_api_key(paths)
            runner.run(
                ["systemctl", "enable", "--now", "robopark-ai.service"],
                timeout=60,
            )
        else:
            runner.run(
                ["systemctl", "disable", "--now", "robopark-ai.service"],
                timeout=60,
            )
