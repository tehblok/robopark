"""Local, explicit model inventory. Registration is not hardware acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

MAX_FILES = 8192
MAX_BYTES = 48 * 1024**3
SUFFIXES = {
    "checkpoint": {".json", ".safetensors", ".model", ".txt", ".jinja", ".tiktoken"},
    "adapter": {".json", ".safetensors"},
    "onnx": {".onnx"},
}


def model_id(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", value) or ".." in value:
        raise ValueError("model_id_invalid")
    return value


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def read_json(path: Path, limit: int = 2 * 1024**2) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("manifest_invalid")
    with path.open("rb") as source:
        value = json.loads(source.read(limit + 1))
    if not isinstance(value, dict):
        raise ValueError("manifest_invalid")
    return value


def write_json(path: Path, value: dict, *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            if path.is_symlink():
                raise ValueError("manifest_symlink")
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _inventory(path: Path, kind: str) -> dict:
    if kind not in SUFFIXES:
        raise ValueError("model_kind_invalid")
    if path.is_symlink():
        raise ValueError("artifact_symlink")
    path = path.resolve(strict=True)
    if kind == "onnx" and not path.is_file():
        raise ValueError("onnx_file_required")
    if kind != "onnx" and not path.is_dir():
        raise ValueError("checkpoint_directory_required")
    files = []
    if path.is_file():
        files.append(path)
    else:
        # Never follow symlinks, including symlink directories. Bound directory
        # entries as well as accepted files before calculating large hashes.
        entries = 0
        for directory, dirs, names in os.walk(path, followlinks=False):
            entries += len(dirs) + len(names)
            if entries > MAX_FILES:
                raise ValueError("artifact_too_many_files")
            for name in dirs + names:
                if (Path(directory) / name).is_symlink():
                    raise ValueError("artifact_symlink")
            files.extend(Path(directory) / name for name in sorted(names))
    if not files:
        raise ValueError("artifact_empty")
    if any(p.suffix not in SUFFIXES[kind] or not p.is_file() for p in files):
        raise ValueError("artifact_type_not_allowed")
    if sum(p.stat().st_size for p in files) > MAX_BYTES:
        raise ValueError("artifact_too_large")
    if kind == "checkpoint":
        config = read_json(path / "config.json")
        if not isinstance(config.get("model_type"), str) or config.get("auto_map"):
            raise ValueError("checkpoint_custom_code_not_supported")
        if not any(p.suffix == ".safetensors" for p in files):
            raise ValueError("checkpoint_weights_missing")
        if not any((path / name).is_file() for name in ("tokenizer.json", "tokenizer.model")):
            raise ValueError("checkpoint_tokenizer_missing")
        model_type = config["model_type"]
    elif kind == "adapter":
        config = read_json(path / "adapter_config.json")
        if (
            not config.get("base_model_name_or_path")
            or not (path / "adapter_model.safetensors").is_file()
        ):
            raise ValueError("adapter_incomplete")
        model_type = config.get("peft_type", "unknown")
    else:
        model_type = "onnx"
    records = {
        p.name if path.is_file() else p.relative_to(path).as_posix(): {
            "size": p.stat().st_size,
            "sha256": digest(p),
        }
        for p in sorted(files)
    }
    fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
    return {"path": str(path), "files": records, "sha256": fingerprint, "model_type": model_type}


def register(state: Path, name: str, kind: str, path: Path) -> dict:
    name = model_id(name)
    value = {
        "schema": 1,
        "id": name,
        "kind": kind,
        "state": "registered_not_accepted",
        **_inventory(path, kind),
    }
    write_json(state / "registry" / f"{name}.json", value)
    return value


def verify(state: Path, name: str) -> dict:
    target = state / "registry" / f"{model_id(name)}.json"
    if not target.exists():
        raise ValueError("model_not_registered")
    value = read_json(target)
    if value.get("schema") != 1 or value.get("id") != name:
        raise ValueError("manifest_invalid")
    current = _inventory(Path(value["path"]), value["kind"])
    if any(current[key] != value.get(key) for key in current):
        raise ValueError("artifact_changed")
    return value


def configure_edge(state: Path, name: str, *, parser: str = "auto") -> dict:
    value = verify(state, name)
    if value["kind"] != "checkpoint":
        raise ValueError("edge_requires_checkpoint_adapter_must_be_merged")
    if not Path(value["path"]).is_relative_to((state / "models").resolve()):
        raise ValueError("edge_checkpoint_requires_managed_models_directory")
    if parser not in {"auto", "generic", "hermes", "qwen3_xml", "nemotron", "openai"}:
        raise ValueError("tool_parser_invalid")
    profile = {
        "schema": 1,
        "runtime": "tensorrt-edge-llm",
        "runtime_version": "0.11.0",
        "model_id": name,
        "model_sha256": value["sha256"],
        "tool_parser": parser,
        "host": "127.0.0.1",
        "port": 18082,
        "max_input_len": 4096,
        "max_kv_cache_capacity": 8192,
        "max_batch_size": 1,
        "adapter": None,
        "rag": {"enabled": False, "mode": "application_context"},
    }
    write_json(state / "edge-profile.json", profile, replace=True)
    return profile
