from conftest import login_as
from test_ai import enable_host


def test_prepared_runtime_waits_for_model_and_keeps_prompt_setup_available(
    client, seed_admin, test_settings, tmp_path
):
    import json

    state = enable_host(test_settings, tmp_path)
    value = json.loads(state.read_text())
    value.update(
        runtime_installed=True,
        model_available=False,
        installed=False,
        enabled=False,
        ready=False,
        reason="awaiting_model",
        backend=None,
    )
    state.write_text(json.dumps(value))
    login_as(client, "admin", "secret")
    status = client.get("/ai/status").json()
    assert status["runtime_installed"] is True
    assert status["model_available"] is False
    assert status["ready"] is status["enabled"] is False
    assert status["reason"] == "awaiting_model"
    assert client.get("/ai/prompts").status_code == 200


def test_retired_knowledge_cannot_be_loaded_or_recreated(
    client, seed_admin, test_settings, tmp_path
):
    enable_host(test_settings, tmp_path)
    login_as(client, "admin", "secret")
    for method, path in [
        ("GET", "/ai/documents"),
        ("POST", "/ai/documents"),
        ("POST", "/ai/documents/import"),
        ("GET", "/ai/documents/old"),
        ("PATCH", "/ai/documents/old"),
        ("DELETE", "/ai/documents/old"),
    ]:
        result = client.request(method, path, json={})
        assert result.status_code == 410
        assert result.json()["detail"] == "ai_knowledge_removed"
    status = client.get("/ai/status").json()
    assert status["counts"]["documents"] == status["counts"]["candidates"] == 0
    assert "knowledge_bundle" not in status


def test_removed_learning_cannot_be_enabled(client, seed_admin, test_settings, tmp_path):
    enable_host(test_settings, tmp_path)
    login_as(client, "admin", "secret")
    config = client.get("/ai/config").json()
    assert config["learning_enabled"] is False
    response = client.patch(
        "/ai/config", json={"revision": config["revision"], "learning_enabled": True}
    )
    assert response.status_code == 410


def test_only_verified_registered_model_identity_is_accepted(
    client, seed_admin, test_settings, tmp_path
):
    import json

    state = enable_host(test_settings, tmp_path)
    value = json.loads(state.read_text())
    value.update(
        model="robopark/gemma-repair-v1",
        model_source="registered",
        model_family="gemma-4-E4B",
        model_sha256="a" * 64,
        parallel_slots=4,
    )
    state.write_text(json.dumps(value))
    login_as(client, "admin", "secret")
    status = client.get("/ai/status").json()
    assert status["ready"] is True
    assert status["model"] == "robopark/gemma-repair-v1"
    assert status["parallel_slots"] == 4
    value.pop("model_sha256")
    state.write_text(json.dumps(value))
    assert client.get("/ai/status").json()["reason"] == "runtime_model_mismatch"
