from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
from pathlib import Path

import pytest
import yaml


def _entrypoint_module():
    path = Path("apps/bot/entrypoint.py")
    spec = importlib.util.spec_from_file_location("robopark_bot_entrypoint", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bot_fetches_telegram_token_from_main_api_without_writing_it(
    tmp_path, monkeypatch
):
    module = _entrypoint_module()
    bridge_key = "runtime-generated-test-key"
    key_file = tmp_path / "bot-bridge-key"
    key_file.write_text(bridge_key, encoding="utf-8")
    key_file.chmod(0o600)
    observed: dict[str, object] = {}
    payload = json.dumps({"token": "telegram-test-token"}).encode()

    class Response(io.BytesIO):
        def __init__(self, value):
            super().__init__(value)
            self.headers = {"Content-Length": str(len(value))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    class Opener:
        def open(self, request, timeout):
            observed["url"] = request.full_url
            observed["bridge_key"] = request.get_header("X-robopark-bot-key")
            observed["timeout"] = timeout
            return Response(payload)

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    token = module.fetch_telegram_token(
        api_base_url="http://api:8000",
        bridge_key_file=key_file,
    )

    assert token == "telegram-test-token"
    assert observed == {
        "url": "http://api:8000/internal/bot/telegram-token",
        "bridge_key": bridge_key,
        "timeout": 10,
    }
    assert list(tmp_path.iterdir()) == [key_file]
    assert "telegram-test-token" not in key_file.read_text(encoding="utf-8")


def test_bot_rejects_unsafe_bridge_key_file(tmp_path):
    module = _entrypoint_module()
    target = tmp_path / "target"
    target.write_text("bridge-key", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "bot-bridge-key"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="unsafe_bot_bridge_key"):
        module.read_bridge_key(link)

    link.unlink()
    link.write_text("bridge-key", encoding="utf-8")
    link.chmod(0o644)
    with pytest.raises(ValueError, match="unsafe_bot_bridge_key"):
        module.read_bridge_key(link)


def test_bot_rejects_malformed_token_payload(tmp_path, monkeypatch):
    module = _entrypoint_module()
    key_file = tmp_path / "bot-bridge-key"
    key_file.write_text("runtime-generated-test-key", encoding="utf-8")
    key_file.chmod(0o600)

    class Response(io.BytesIO):
        def __init__(self, value):
            super().__init__(value)
            self.headers = {"Content-Length": str(len(value))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    class Opener:
        def open(self, _request, _timeout=None, **_kwargs):
            return Response(b"[]")

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(RuntimeError, match="telegram_token_unavailable"):
        module.fetch_telegram_token(
            api_base_url="http://api:8000",
            bridge_key_file=key_file,
        )


def test_bot_entrypoint_runs_only_the_native_scheduler():
    source = Path("apps/bot/entrypoint.py").read_text()
    assert "from native.runtime import run_service" in source
    assert "meeting_reminders.py" not in source
    assert "telegram_sender.py" not in source
    assert "subprocess.Popen" not in source


def test_bot_heartbeat_is_private_and_rejects_symlinks(tmp_path):
    module = _entrypoint_module()
    heartbeat = tmp_path / "runtime-heartbeat"

    descriptor = module.open_runtime_heartbeat(tmp_path)
    try:
        assert heartbeat.read_bytes() == b"1\n"
        assert stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o600
        assert os.fstat(descriptor).st_mtime == 0
    finally:
        os.close(descriptor)

    heartbeat.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"safe\n")
    heartbeat.symlink_to(outside)
    with pytest.raises(OSError):
        module.open_runtime_heartbeat(tmp_path)
    assert outside.read_bytes() == b"safe\n"


def test_bot_compose_is_opt_in_and_has_no_host_control_surface():
    compose = yaml.safe_load(Path("deploy/docker-compose.yml").read_text())
    bot = compose["services"]["bot"]

    assert bot["profiles"] == ["bot"]
    assert bot["depends_on"] == {"api": {"condition": "service_healthy"}}
    assert bot["user"] == "10001:10001"
    assert bot["environment"] == {
        "ROBOPARK_API_URL": "http://api:8000",
        "ROBOPARK_BOT_BRIDGE_KEY_FILE": "/run/secrets/bot-bridge-key",
        "ROBOPARK_BOT_DATA_DIR": "/data/telegram-bot",
        "TZ": "Europe/Moscow",
    }
    assert bot.get("ports") is None
    assert bot.get("privileged") is None
    assert bot.get("network_mode") is None
    assert bot["volumes"] == [
        "${ROBOPARK_DATA_SOURCE:-robopark_data}:/data",
        "${ROBOPARK_BOT_BRIDGE_KEY_FILE:-/etc/robopark/bot-bridge-key}:/run/secrets/bot-bridge-key:ro",
    ]
    serialized = json.dumps(bot)
    assert "/var/run/docker.sock" not in serialized
    assert "/host-repo" not in serialized
    assert "TRACKER_TOKEN" not in serialized

    api = compose["services"]["api"]
    assert api["environment"]["ROBOPARK_BOT_BRIDGE_KEY_FILE"] == (
        "/run/secrets/bot-bridge-key"
    )
    assert (
        "${ROBOPARK_BOT_BRIDGE_KEY_FILE:-/etc/robopark/bot-bridge-key}:/run/secrets/bot-bridge-key:ro"
        in api["volumes"]
    )


def test_bot_image_runs_unprivileged_without_embedding_runtime_data():
    dockerfile = Path("apps/bot/Dockerfile").read_text(encoding="utf-8")
    dockerignore = Path("apps/bot/.dockerignore").read_text(encoding="utf-8")
    assert "USER robopark" in dockerfile
    assert "COPY native ./native" in dockerfile
    assert "COPY legacy/app" not in dockerfile
    assert "COPY legacy/data" not in dockerfile
    assert "linux/amd64" not in dockerfile
    assert "linux/arm64" not in dockerfile
    assert "TELEGRAM_BOT_TOKEN=" not in dockerfile
    assert "TRACKER_TOKEN=" not in dockerfile
    assert "/tmp/bot-ready" in dockerfile
    assert "legacy/data" in dockerignore.splitlines()
    assert "**/__pycache__" in dockerignore.splitlines()
    assert "*.pyc" in dockerignore.splitlines()
