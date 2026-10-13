from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy/accelerator"))
from robopark_accel import registry


def checkpoint(path):
    path.mkdir()
    (path / "config.json").write_text('{"model_type":"qwen3"}')
    (path / "tokenizer.json").write_text("{}")
    (path / "model.safetensors").write_bytes(b"test-weights")
    return path


def test_registration_does_not_assume_gemma_and_detects_changed_weights(tmp_path):
    model = checkpoint(tmp_path / "qwen")
    store = tmp_path / "state"
    result = registry.register(store, "qwen-experiment", "checkpoint", model)
    assert result["model_type"] == "qwen3"
    assert result["state"] == "registered_not_accepted"
    assert registry.verify(store, "qwen-experiment") == result
    (model / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="artifact_changed"):
        registry.verify(store, "qwen-experiment")


def test_registration_rejects_symlink_code_pickle_and_duplicate(tmp_path):
    model = checkpoint(tmp_path / "model")
    store = tmp_path / "state"
    (model / "custom.py").write_text("raise RuntimeError")
    with pytest.raises(ValueError, match="artifact_type_not_allowed"):
        registry.register(store, "model", "checkpoint", model)
    (model / "custom.py").unlink()
    (model / "link.json").symlink_to(model / "config.json")
    with pytest.raises(ValueError, match="artifact_symlink"):
        registry.register(store, "model", "checkpoint", model)
    (model / "link.json").unlink()
    registry.register(store, "model", "checkpoint", model)
    with pytest.raises(FileExistsError):
        registry.register(store, "model", "checkpoint", model)


def test_profile_needs_model_and_raw_adapter_cannot_be_silently_ignored(tmp_path):
    with pytest.raises(ValueError, match="model_not_registered"):
        registry.configure_edge(tmp_path, "missing")
    (tmp_path / "models").mkdir()
    model = checkpoint(tmp_path / "models/base")
    registry.register(tmp_path, "base", "checkpoint", model)
    profile = registry.configure_edge(tmp_path, "base")
    assert profile["model_id"] == "base"
    assert profile["rag"]["enabled"] is False
    assert profile["adapter"] is None
    assert json.loads((tmp_path / "edge-profile.json").read_text()) == profile


@pytest.mark.parametrize("name", ["../model", "", "a/b", "a\n", "-flag"])
def test_name_cannot_escape_registry(tmp_path, name):
    with pytest.raises(ValueError, match="model_id_invalid"):
        registry.verify(tmp_path, name)
