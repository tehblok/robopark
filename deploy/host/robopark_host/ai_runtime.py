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
import struct
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import ClassVar

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
MODEL_FAMILY = "gemma-4-E4B"
PARALLEL_SLOTS = 4
CONTEXT_TOKENS_PER_SLOT = 8192
TOTAL_CONTEXT_TOKENS = PARALLEL_SLOTS * CONTEXT_TOKENS_PER_SLOT
ACTIVE_MODEL_FILE = "active.gguf"
MIN_REGISTERED_MODEL_SIZE = 4 * 1024**3
MAX_REGISTERED_MODEL_SIZE = 8 * 1024**3
MAX_GGUF_METADATA_BYTES = 24 * 1024**2
EXPECTED_GGUF_TENSORS = 666
EXPECTED_GGUF_METADATA = {
    "general.architecture": "gemma4",
    "general.type": "model",
    "gemma4.block_count": 42,
    "gemma4.context_length": 131072,
    "gemma4.embedding_length": 2560,
    "gemma4.feed_forward_length": 10240,
    "gemma4.attention.head_count": 8,
    "gemma4.attention.head_count_kv": 2,
    "tokenizer.ggml.model": "gemma4",
}
_startup_smoke_lock = threading.Lock()
_startup_smoke_identity = None


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
    try:
        selection = selected_model(paths)
    except ValueError:
        selection = _builtin_model()
    return {
        "schema": 1,
        "supported": bool(supported),
        "installed": bool(installed),
        "runtime_installed": runtime_installed(paths),
        "model_available": model_available(paths),
        "enabled": bool(enabled),
        "ready": bool(ready),
        "reason": reason,
        "model": selection["model"],
        "model_family": MODEL_FAMILY,
        "model_source": selection["model_source"],
        "model_sha256": (
            selection["model_sha256"]
            if selection["model_source"] == "registered"
            else model_sha256
        ),
        "backend": backend,
        "parallel_slots": PARALLEL_SLOTS,
        "context_tokens_per_slot": CONTEXT_TOKENS_PER_SLOT,
        "total_context_tokens": TOTAL_CONTEXT_TOKENS,
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


def _valid_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_model_id(value: str) -> bool:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/"
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value[0].isalnum()
        and value[-1].isalnum()
        and all(character in allowed for character in value)
        and ".." not in value
        and "//" not in value
    )


class _GGUFReader:
    _SCALAR_SIZES: ClassVar[dict[int, int]] = {
        0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1,
        10: 8, 11: 8, 12: 8,
    }

    def __init__(self, data: bytes):
        self.data = memoryview(data)
        self.offset = 0

    def read(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise ValueError("ai_model_gguf_metadata_invalid")
        value = bytes(self.data[self.offset : self.offset + size])
        self.offset += size
        return value

    def u32(self) -> int:
        return struct.unpack("<I", self.read(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.read(8))[0]

    def string(self, *, maximum=1024 * 1024) -> str:
        size = self.u64()
        if size > maximum:
            raise ValueError("ai_model_gguf_metadata_invalid")
        try:
            return self.read(size).decode("utf-8", "strict")
        except UnicodeError as error:
            raise ValueError("ai_model_gguf_metadata_invalid") from error

    def value(self, value_type: int, *, retain: bool):
        if value_type == 8:
            value = self.string()
            return value if retain else None
        if value_type == 9:
            element_type = self.u32()
            count = self.u64()
            if count > 1_000_000 or element_type == 9:
                raise ValueError("ai_model_gguf_metadata_invalid")
            if element_type in self._SCALAR_SIZES:
                self.read(count * self._SCALAR_SIZES[element_type])
            elif element_type == 8:
                for _ in range(count):
                    self.string()
            else:
                raise ValueError("ai_model_gguf_metadata_invalid")
            return None
        size = self._SCALAR_SIZES.get(value_type)
        if size is None:
            raise ValueError("ai_model_gguf_metadata_invalid")
        raw = self.read(size)
        if not retain:
            return None
        if value_type == 4:
            return struct.unpack("<I", raw)[0]
        raise ValueError("ai_model_gguf_metadata_invalid")


def _validate_gemma4_gguf(header: bytes, file_size: int) -> None:
    if not MIN_REGISTERED_MODEL_SIZE <= file_size <= MAX_REGISTERED_MODEL_SIZE:
        raise ValueError("ai_model_size_invalid")
    reader = _GGUFReader(header)
    if reader.read(4) != b"GGUF":
        raise ValueError("ai_model_not_gguf")
    if reader.u32() != 3 or reader.u64() != EXPECTED_GGUF_TENSORS:
        raise ValueError("ai_model_architecture_invalid")
    metadata_count = reader.u64()
    if not 1 <= metadata_count <= 256:
        raise ValueError("ai_model_gguf_metadata_invalid")
    wanted = {*EXPECTED_GGUF_METADATA, "tokenizer.chat_template"}
    metadata = {}
    seen = set()
    for _ in range(metadata_count):
        key = reader.string(maximum=256)
        if key in seen:
            raise ValueError("ai_model_gguf_metadata_invalid")
        seen.add(key)
        value_type = reader.u32()
        retained = reader.value(value_type, retain=key in wanted)
        if key in wanted:
            metadata[key] = retained
    if any(metadata.get(key) != value for key, value in EXPECTED_GGUF_METADATA.items()):
        raise ValueError("ai_model_architecture_invalid")
    template = metadata.get("tokenizer.chat_template")
    if (
        not isinstance(template, str)
        or not 32 <= len(template.encode("utf-8")) <= 1024 * 1024
        or any(marker not in template for marker in ("tools", "<|tool_call>", "<|tool_response>"))
    ):
        raise ValueError("ai_model_chat_template_invalid")


def _builtin_model(*, selected=True) -> dict:
    return {
        "schema": 1,
        "model": MODEL_ID,
        "model_family": MODEL_FAMILY,
        "model_source": "builtin",
        "model_file": MODEL_FILE,
        "model_size": MODEL_SIZE,
        "model_sha256": MODEL_SHA256,
        "selected": selected,
    }


def _registration_path(paths, model_id: str) -> Path:
    identity = hashlib.sha256(model_id.encode("ascii")).hexdigest()
    return paths.var / "ai/models/registrations" / f"{identity}.json"


def _validate_registered_record(paths, value, *, verify: bool) -> dict:
    expected_keys = {
        "schema", "model", "model_family", "model_source", "model_file",
        "model_size", "model_sha256", "selected",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schema") != 1
        or not _valid_model_id(value.get("model"))
        or value.get("model_family") != MODEL_FAMILY
        or value.get("model_source") != "registered"
        or value.get("selected") not in {True, False}
        or type(value.get("model_size")) is not int
        or not MIN_REGISTERED_MODEL_SIZE <= value["model_size"] <= MAX_REGISTERED_MODEL_SIZE
        or not _valid_sha256(value.get("model_sha256"))
        or value.get("model_file") != f"registered/{value['model_sha256']}.gguf"
    ):
        raise ValueError("ai_model_registration_invalid")
    target = paths.var / "ai/models" / value["model_file"]
    try:
        info = target.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size != value["model_size"]
        ):
            raise ValueError("ai_model_registration_invalid")
        if verify:
            with target.open("rb") as stream:
                header = stream.read(MAX_GGUF_METADATA_BYTES)
            _validate_gemma4_gguf(header, info.st_size)
            if _sha256(target) != value["model_sha256"]:
                raise ValueError("ai_model_checksum_mismatch")
    except OSError as error:
        raise ValueError("ai_model_registration_invalid") from error
    return dict(value)


def selected_model(paths, *, verify=False) -> dict:
    target = paths.var / "ai/model-selection.json"
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ValueError("ai_model_not_selected")
    except OSError as error:
        raise ValueError("ai_model_selection_invalid") from error
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            expected_uid = 0 if paths.root == Path("/") else os.geteuid()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != expected_uid
                or info.st_size > 4096
            ):
                raise ValueError("ai_model_selection_invalid")
            value = json.loads(stream.read(4097))
    except (OSError, ValueError, UnicodeError) as error:
        raise ValueError("ai_model_selection_invalid") from error
    if value == _builtin_model():
        if verify:
            model = paths.var / "ai/models" / MODEL_FILE
            if (
                model.is_symlink()
                or not model.is_file()
                or model.stat().st_size != MODEL_SIZE
                or _sha256(model) != MODEL_SHA256
            ):
                raise ValueError("ai_model_not_installed")
        return value
    try:
        return _validate_registered_record(paths, value, verify=verify)
    except ValueError as error:
        raise ValueError("ai_model_selection_invalid") from error


def register_merged_gguf(paths, source, *, sha256: str, model_id: str) -> dict:
    """Copy one explicitly verified merged GGUF without selecting it."""
    source = Path(source)
    if not source.is_absolute() or not _valid_sha256(sha256) or not _valid_model_id(model_id):
        raise ValueError("ai_model_registration_invalid")
    ensure_service_account(paths)
    with ai_lock(paths):
        models = paths.var / "ai/models"
        registered = models / "registered"
        registrations = models / "registrations"
        models.mkdir(parents=True, exist_ok=True, mode=0o750)
        info = models.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("ai_model_registration_invalid")
        for directory in (registered, registrations):
            directory.mkdir(exist_ok=True, mode=0o750)
            info = directory.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("ai_model_registration_invalid")
        target = registered / f"{sha256}.gguf"
        temporary = registered / f".{sha256}.{secrets.token_hex(8)}.part"
        source_descriptor = None
        output_descriptor = None
        digest = hashlib.sha256()
        header = bytearray()
        size = 0
        try:
            source_descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            info = os.fstat(source_descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or not MIN_REGISTERED_MODEL_SIZE <= info.st_size <= MAX_REGISTERED_MODEL_SIZE
            ):
                raise ValueError("ai_model_registration_invalid")
            output_descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o640,
            )
            while chunk := os.read(source_descriptor, 1024 * 1024):
                if len(header) < MAX_GGUF_METADATA_BYTES:
                    remaining = MAX_GGUF_METADATA_BYTES - len(header)
                    header.extend(chunk[:remaining])
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(output_descriptor, view)
                    view = view[written:]
            if digest.hexdigest() != sha256:
                raise ValueError("ai_model_checksum_mismatch")
            _validate_gemma4_gguf(bytes(header), size)
            os.fsync(output_descriptor)
            os.close(output_descriptor)
            output_descriptor = None
            os.chmod(temporary, 0o640)
            os.replace(temporary, target)
        finally:
            if source_descriptor is not None:
                os.close(source_descriptor)
            if output_descriptor is not None:
                os.close(output_descriptor)
            temporary.unlink(missing_ok=True)
        receipt = {
            "schema": 1,
            "model": model_id,
            "model_family": MODEL_FAMILY,
            "model_source": "registered",
            "model_file": f"registered/{sha256}.gguf",
            "model_size": size,
            "model_sha256": sha256,
            "selected": False,
        }
        for path in registrations.glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if record.get("model") == model_id and record != receipt:
                raise ValueError("ai_model_id_conflict")
        if paths.root == Path("/"):
            account = pwd.getpwnam("robopark-ai")
            os.chown(registered, 0, account.pw_gid)
            registered.chmod(0o750)
            os.chown(target, 0, account.pw_gid)
        target.chmod(0o640)
        atomic_write_json(_registration_path(paths, model_id), receipt)
        return receipt


def _registered_model(paths, model_id: str) -> dict:
    registrations = paths.var / "ai/models/registrations"
    matches = []
    for path in registrations.glob("*.json"):
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if value.get("model") == model_id:
            matches.append(_validate_registered_record(paths, value, verify=True))
    if len(matches) != 1:
        raise ValueError("ai_model_not_registered")
    return {**matches[0], "selected": True}


def _activate_model(paths, selection: dict) -> None:
    models = paths.var / "ai/models"
    models.mkdir(parents=True, exist_ok=True, mode=0o750)
    active = models / ACTIVE_MODEL_FILE
    temporary = models / f".{ACTIVE_MODEL_FILE}.{secrets.token_hex(8)}"
    try:
        os.symlink(selection["model_file"], temporary)
        os.replace(temporary, active)
    finally:
        temporary.unlink(missing_ok=True)
    atomic_write_json(paths.var / "ai/model-selection.json", selection)
    atomic_write_json(paths.var / "ai/runtime-model.json", {
        "schema": 1,
        "llama_commit": LLAMA_COMMIT,
        "model": selection["model"],
        "model_sha256": selection["model_sha256"],
        "model_source": selection["model_source"],
    })


def _clear_active_model(paths) -> None:
    for target in (
        paths.var / "ai/model-selection.json",
        paths.var / "ai/runtime-model.json",
        paths.var / "ai/models" / ACTIVE_MODEL_FILE,
    ):
        target.unlink(missing_ok=True)


def select_model(paths, model_id: str, runner) -> dict:
    """Explicitly switch the verified model while serializing runtime control."""
    if not _valid_model_id(model_id):
        raise ValueError("ai_model_selection_invalid")
    with ai_control_lock(paths), ai_lock(paths):
        try:
            previous = selected_model(paths, verify=True)
            previous_available = True
        except ValueError as error:
            if str(error) not in {"ai_model_not_installed", "ai_model_not_selected"}:
                raise
            previous = None
            previous_available = False
        selection = (
            _builtin_model()
            if model_id == MODEL_ID
            else _registered_model(paths, model_id)
        )
        if selection["model_source"] == "builtin":
            model = paths.var / "ai/models" / MODEL_FILE
            if not model.is_file() or model.stat().st_size != MODEL_SIZE or _sha256(model) != MODEL_SHA256:
                raise ValueError("ai_model_not_installed")
        enabled = read_enabled_intent(paths) is True
        runner.run(["systemctl", "stop", "robopark-ai.service"], timeout=60)
        failure = None
        failure_code = "ai_model_selection_failed"
        try:
            _activate_model(paths, selection)
            if enabled:
                failure_code = "ai_model_startup_failed"
                _clear_startup_smoke_cache()
                runner.run(["systemctl", "start", "robopark-ai.service"], timeout=60)
                ready, reason = _wait_runtime_ready(paths)
                if not ready:
                    raise ValueError(reason or "startup_failed")
                publish_status(
                    paths, supported=True, installed=installed(paths), enabled=True,
                    ready=True, reason=None, model_sha256=selection["model_sha256"],
                    backend="cuda",
                )
            else:
                publish_status(
                    paths, supported=True, installed=installed(paths), enabled=False,
                    ready=False, reason="disabled", model_sha256=selection["model_sha256"],
                )
            return selection
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            failure = error
        rollback_failure = None
        try:
            if enabled:
                runner.run(["systemctl", "stop", "robopark-ai.service"], timeout=60)
            if previous_available:
                _activate_model(paths, previous)
            else:
                _clear_active_model(paths)
            if enabled and previous_available:
                _clear_startup_smoke_cache()
                runner.run(["systemctl", "start", "robopark-ai.service"], timeout=60)
                rollback_ready, rollback_reason = _wait_runtime_ready(paths)
                if not rollback_ready:
                    raise RuntimeError(rollback_reason or "rollback_failed")
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            rollback_failure = error
        if rollback_failure is not None:
            with suppress(OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                publish_status(
                    paths, supported=True, installed=installed(paths), enabled=enabled,
                    ready=False, reason="rollback_failed",
                    model_sha256=previous["model_sha256"] if previous else None,
                )
            raise RuntimeError("ai_model_rollback_failed") from rollback_failure
        with suppress(OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            publish_status(
                paths, supported=True, installed=installed(paths),
                enabled=enabled and previous_available,
                ready=enabled and previous_available,
                reason=(None if enabled else "disabled") if previous_available else "awaiting_model",
                model_sha256=previous["model_sha256"] if previous else None,
                backend="cuda" if enabled and previous_available else None,
            )
        raise ValueError(failure_code) from failure


def install_runtime(paths) -> None:
    """Build the pinned inference runtime without downloading model weights."""
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

    if paths.root == Path("/"):
        account = pwd.getpwnam("robopark-ai")
        for directory in (base, binary.parent, models):
            os.chown(directory, 0, account.pw_gid)
            directory.chmod(0o750)
        os.chown(base / "bin/llama-server", 0, account.pw_gid)
    for legacy_file in LEGACY_MODEL_FILES:
        for suffix in ("", ".part"):
            with suppress(FileNotFoundError):
                (models / (legacy_file + suffix)).unlink()

    atomic_write_json(
        base / "install.json",
        {
            "schema": 3,
            "llama_commit": LLAMA_COMMIT,
            "binary_sha256": _sha256(binary),
        },
    )


def runtime_installed(paths, *, verify=False) -> bool:
    base = paths.var / "ai"
    binary = base / "bin/llama-server"
    if binary.is_symlink() or not binary.is_file():
        return False
    receipt = base / "install.json"
    try:
        record = json.loads(receipt.read_text())
    except (OSError, ValueError):
        record = None
    binary_hash = record.get("binary_sha256") if isinstance(record, dict) else None
    if not isinstance(binary_hash, str) or len(binary_hash) != 64 or any(c not in "0123456789abcdef" for c in binary_hash):
        return False
    current = {
        "schema": 3,
        "llama_commit": LLAMA_COMMIT,
        "binary_sha256": binary_hash,
    }
    legacy = {
        "schema": 2,
        "llama_commit": LLAMA_COMMIT,
        "binary_sha256": binary_hash,
        "model_file": MODEL_FILE,
        "model_size": MODEL_SIZE,
        "model_sha256": MODEL_SHA256,
    }
    if record not in (current, legacy):
        # A model checksum cannot certify the installed server's build revision.
        # Only install_runtime may write a receipt for a newly built runtime.
        return False
    return not verify or _sha256(binary) == binary_hash


def model_available(paths, *, verify=False) -> bool:
    try:
        selected_model(paths, verify=verify)
    except (OSError, ValueError):
        return False
    selection = selected_model(paths)
    target = paths.var / "ai/models" / selection["model_file"]
    return target.is_file() and not target.is_symlink()


def installed(paths, *, verify=False) -> bool:
    base = paths.var / "ai"
    if not runtime_installed(paths, verify=verify):
        return False
    try:
        selection = selected_model(paths, verify=verify)
        active = base / "models" / ACTIVE_MODEL_FILE
        selected_path = base / "models" / selection["model_file"]
        runtime_record = json.loads((base / "runtime-model.json").read_text())
        if (
            not active.is_symlink()
            or active.resolve(strict=True) != selected_path.resolve(strict=True)
            or runtime_record != {
                "schema": 1,
                "llama_commit": LLAMA_COMMIT,
                "model": selection["model"],
                "model_sha256": selection["model_sha256"],
                "model_source": selection["model_source"],
            }
        ):
            return False
        return not verify or (
            _sha256(selected_path) == selection["model_sha256"]
        )
    except (OSError, ValueError):
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


def _clear_startup_smoke_cache() -> None:
    global _startup_smoke_identity
    with _startup_smoke_lock:
        _startup_smoke_identity = None


def _runtime_process_identity(paths):
    if paths.root != Path("/"):
        return (str(paths.root), 1)
    result = subprocess.run(
        [
            "systemctl", "show", "--property=MainPID", "--value",
            "robopark-ai.service",
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        pid = int(result.stdout.strip())
    except ValueError:
        return None
    return pid if pid > 0 else None


def _runtime_identity(paths, api_key: str):
    process = _runtime_process_identity(paths)
    if process is None:
        return None
    request = urllib.request.Request(
        "http://127.0.0.1:18081/props",
        headers={"Authorization": "Bearer " + api_key},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            return None
        props = json.loads(raw)
    if not isinstance(props, dict):
        return None
    settings = props.get("default_generation_settings")
    if (
        props.get("total_slots") != PARALLEL_SLOTS
        or props.get("model_path") != str(paths.var / "ai/models" / ACTIVE_MODEL_FILE)
        or not isinstance(settings, dict)
        or settings.get("n_ctx") != CONTEXT_TOKENS_PER_SLOT
    ):
        return None
    selection = selected_model(paths)
    return process, selection["model_sha256"]


def runtime_ready(paths) -> tuple[bool, str | None]:
    if not any((paths.root / name).exists() for name in ("dev/nvidia0", "dev/nvhost-gpu")):
        return False, "cuda_device_unavailable"
    try:
        api_key = read_api_key(paths)
        health = urllib.request.Request(
            "http://127.0.0.1:18081/health",
            headers={"Authorization": "Bearer " + api_key},
        )
        with urllib.request.urlopen(health, timeout=3) as response:
            if response.status != 200:
                return False, "health_failed"
    except (OSError, RuntimeError, ValueError, KeyError):
        return False, "startup_unreachable"
    try:
        identity = _runtime_identity(paths, api_key)
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError):
        return False, "identity_failed"
    if identity is None:
        return False, "identity_failed"
    global _startup_smoke_identity
    with _startup_smoke_lock:
        if _startup_smoke_identity == identity:
            return True, None
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:18081/v1/chat/completions",
                # This synthetic function is never dispatched. It checks the actual
                # system/tool template and JSON wire contract once per server process.
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
                    "Authorization": "Bearer " + api_key,
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
        _startup_smoke_identity = identity
    return True, None


def _wait_runtime_ready(paths, *, timeout=120, interval=2) -> tuple[bool, str | None]:
    deadline = time.monotonic() + timeout
    last = (False, "startup_unreachable")
    while True:
        last = runtime_ready(paths)
        if last[0] or time.monotonic() >= deadline:
            return last
        time.sleep(interval)


def reconcile(paths, *, auto_install=False, runner=None):
    supported, reason = probe_support(paths)
    if not supported:
        return publish_status(paths, supported=False, reason=reason)
    with ai_lock(paths):
        try:
            supported, reason = probe_support(paths)
            if not supported:
                return publish_status(paths, supported=False, reason=reason)
            if auto_install and not runtime_installed(paths, verify=True):
                install_runtime(paths)
            present = installed(paths)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            return publish_status(paths, supported=True, installed=installed(paths), reason="install_failed")
        if runner and present:
            try:
                runner.run(["systemctl", "enable", "--now", "robopark-ai.service", "robopark-ai-broker.service"], timeout=60)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                return publish_status(
                    paths, supported=True, installed=True, enabled=False,
                    ready=False, reason="startup_failed", model_sha256=MODEL_SHA256,
                )
        runtime_present = runtime_installed(paths)
        ready, why = runtime_ready(paths) if present else (
            False, "awaiting_model" if runtime_present else "not_installed"
        )
        return publish_status(paths, supported=True, installed=present, enabled=bool(runner and present), ready=ready, reason=why, model_sha256=(selected_model(paths)["model_sha256"] if present else None), backend="cuda" if ready else None)


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
                write_enabled_intent(paths, False)
            runner.run(["systemctl", "start", "--no-block", "robopark-ai-setup.service"], timeout=15)
            return publish_status(paths, supported=True, installed=installed(paths), reason="installing")
        if action == "enable" and not runtime_installed(paths, verify=True):
            raise ValueError("ai_runtime_not_installed")
        if action == "enable" and not installed(paths, verify=True):
            raise ValueError("ai_model_not_selected")
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
                    paths.var / "ai/model-selection.json",
                    paths.var / "ai/runtime-model.json",
                    paths.var / "ai/models" / ACTIVE_MODEL_FILE,
                ):
                    with suppress(FileNotFoundError):
                        target.unlink()
                for directory in (
                    paths.var / "ai/models/registered",
                    paths.var / "ai/models/registrations",
                ):
                    if directory.is_dir() and not directory.is_symlink():
                        shutil.rmtree(directory)
    if not supported:
        return publish_status(paths, supported=False, reason=reason)
    present = installed(paths)
    enabled = action == "enable"
    runtime_present = runtime_installed(paths)
    ready, why = runtime_ready(paths) if enabled and present else (
        False, "disabled" if present else "awaiting_model" if runtime_present else "not_installed"
    )
    return publish_status(
        paths, supported=True, installed=present, enabled=enabled, ready=ready,
        reason=why, model_sha256=(selected_model(paths)["model_sha256"] if present else None),
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
        runtime_present = runtime_installed(paths)
        ready, reason = False, "disabled" if present else "awaiting_model" if runtime_present else "not_installed"
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
