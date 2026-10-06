from pathlib import Path

from robopark_host import cli


def test_register_model_does_not_select_or_restart_it(host_paths, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "paths_from_environment", lambda: host_paths)
    monkeypatch.setattr("robopark_host.storage_compatibility.require_storage_operations", lambda *args, **kwargs: None)
    monkeypatch.setattr("robopark_host.ai_runtime.register_merged_gguf", lambda paths, source, **kwargs: calls.append((paths, source, kwargs)) or {"selected": False})
    monkeypatch.setattr("robopark_host.ai_runtime.select_model", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected selection")))
    assert cli.main(["ai-model-register", "--file", "/models/model.gguf", "--sha256", "a" * 64, "--model-id", "robopark/repair"]) == 0
    assert calls == [(host_paths, Path("/models/model.gguf"), {"sha256": "a" * 64, "model_id": "robopark/repair"})]
    assert 'false' in capsys.readouterr().out


def test_select_model_requires_explicit_id(host_paths, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "paths_from_environment", lambda: host_paths)
    monkeypatch.setattr("robopark_host.storage_compatibility.require_storage_operations", lambda *args, **kwargs: None)
    monkeypatch.setattr("robopark_host.ai_runtime.select_model", lambda paths, model_id, runner: calls.append(model_id) or {"selected": True})
    assert cli.main(["ai-model-select", "--model-id", "robopark/repair"]) == 0
    assert calls == ["robopark/repair"]


def test_ai_setup_succeeds_when_runtime_is_prepared_without_model(
    host_paths, monkeypatch
):
    monkeypatch.setattr(cli, "paths_from_environment", lambda: host_paths)
    monkeypatch.setattr(
        "robopark_host.storage_compatibility.require_storage_operations",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "robopark_host.ai_runtime.reconcile",
        lambda paths, **kwargs: {
            "supported": True,
            "installed": False,
            "runtime_installed": True,
            "reason": "awaiting_model",
        },
    )
    monkeypatch.setattr(
        "robopark_host.ai_runtime.runtime_installed",
        lambda paths, **kwargs: True,
    )

    assert cli.main(["ai-setup"]) == 0
